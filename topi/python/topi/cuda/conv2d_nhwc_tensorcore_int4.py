# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# pylint: disable=invalid-name, too-many-locals, too-many-function-args
# pylint: disable=too-many-statements, unused-argument, too-many-arguments
"""Tensorcore template for cuda backend"""
import numpy as np
import tvm
from tvm import te
from tvm import autotvm
from ..util import get_const_tuple, traverse_inline, simplify
from ..nn.pad import pad
from ..nn.util import get_pad_tuple
# from .tensor_intrin import intrin_wmma_load_matrix_A
# from .tensor_intrin import intrin_wmma_load_matrix_W
# from .tensor_intrin import intrin_wmma_store_matrix
# from .tensor_intrin import intrin_wmma_gemm

def intrin_wmma_load_matrix(scope):
    n = m = 8
    l = 32
    if scope == 'wmma.matrix_a':
        A = tvm.te.placeholder((n, l), name='A', dtype='int4')
        C = tvm.te.compute((n, l), lambda i, j: A[i, j], name='C')
    else:
        A = tvm.te.placeholder((m, l), name='A', dtype='int4')
        C = tvm.te.compute((m, l), lambda i, j: A[i, j], name='C')
    # A = te.placeholder((n, m), name='A', dtype='int4')
    # C = te.compute((m, n), lambda i, j: A[i, j], name='C')
    BA = tvm.tir.decl_buffer(A.shape, A.dtype, scope='shared', data_alignment=32, offset_factor=256)
    BC = tvm.tir.decl_buffer(C.shape, C.dtype, scope=scope, data_alignment=32, offset_factor=256)

    def intrin_func(ins, outs):
        ib = tvm.tir.ir_builder.create()

        BA = ins[0]
        BC = outs[0]
        if scope == "wmma.matrix_a":
            ib.emit(tvm.tir.call_intrin('handle', 'tvm_load_matrix_sync',
                                    BC.data, n, m, l, BC.elem_offset // 256,
                                    BA.access_ptr('r'), l, 'row_major'))
        elif scope == "wmma.matrix_b":
            ib.emit(tvm.tir.call_intrin('handle', 'tvm_load_matrix_sync',
                                    BC.data, n, m, l, BC.elem_offset // 256,
                                    BA.access_ptr('r'), l, 'col_major'))                                    
        return ib.get()

    return te.decl_tensor_intrin(C.op, intrin_func, binds={A: BA, C: BC})


def intrin_wmma_gemm():
    n = m = 8
    l = 32
    A = te.placeholder((n, l), name='A', dtype='int4')
    B = te.placeholder((n, l), name='B', dtype='int4')
    k = te.reduce_axis((0, l), name="k")
    C = te.compute((n, n),
                    lambda ii, jj:
                    te.sum(A[ii, k].astype('int32') * B[jj, k].astype('int32'), axis=k),
                    name='C')
    BA = tvm.tir.decl_buffer(A.shape, A.dtype, name='BA', scope='wmma.matrix_a', data_alignment=32, offset_factor=256)
    BB = tvm.tir.decl_buffer(B.shape, B.dtype, name='BB', scope='wmma.matrix_b', data_alignment=32, offset_factor=256)
    BC = tvm.tir.decl_buffer(C.shape, C.dtype, name='BC', scope='wmma.accumulator', data_alignment=32, offset_factor=64)

    def intrin_func(ins, outs):
        BA, BB = ins
        BC, = outs

        def init():
            ib = tvm.tir.ir_builder.create()
            ib.emit(tvm.tir.call_intrin('handle', 'tvm_fill_fragment', BC.data, n, m, l, BC.elem_offset // n * n, 0.0))
            return ib.get()

        def update():
            ib = tvm.tir.ir_builder.create()
            ib.emit(tvm.tir.call_intrin('handle', 'tvm_mma_sync',
                                    BC.data, BC.elem_offset // 64,
                                    BA.data, BA.elem_offset // 256,
                                    BB.data, BB.elem_offset // 256,
                                    BC.data, BC.elem_offset // 64))
            return ib.get()

        return update(), init(), update()

    return te.decl_tensor_intrin(C.op, intrin_func, binds={A: BA, B: BB, C: BC})


def intrin_wmma_store_matrix():
    n = m = 8
    l = 32
    A = te.placeholder((n, m), name='A', dtype='int32')
    BA = tvm.tir.decl_buffer(A.shape, A.dtype, scope='wmma.accumulator', data_alignment=32, offset_factor=64)
    C = te.compute((n, m), lambda i, j: A[i, j], name='C')
    BC = tvm.tir.decl_buffer(C.shape, C.dtype, scope='global', data_alignment=32, offset_factor=64)

    def intrin_func(ins, outs):
        ib = tvm.tir.ir_builder.create()
        BA = ins[0]
        BC = outs[0]
        ib.emit(tvm.tir.call_intrin('handle', 'tvm_store_matrix_sync',
                                BA.data, n, m, l, BA.elem_offset // 64,
                                BC.access_ptr('w'), n, 'row_major'))
        return ib.get()

    return te.decl_tensor_intrin(C.op, intrin_func, binds={A: BA, C: BC})

def nhwc_tensorcore_cuda(cfg, Input, Filter, stride, padding, dilation, in_dtype, out_dtype):
    """Compute declaration for tensorcore"""
    assert isinstance(stride, int) or len(stride) == 2
    assert isinstance(dilation, int) or len(dilation) == 2

    if isinstance(stride, int):
        stride_h = stride_w = stride
    else:
        stride_h, stride_w = stride

    if isinstance(dilation, int):
        dilation_h = dilation_w = dilation
    else:
        dilation_h, dilation_w = dilation
    
    wmma_n = wmma_m = 8
    wmma_k = 32

    batch, in_height, in_width, in_channels, wmma_m, wmma_k = get_const_tuple(Input.shape)
    if in_dtype == 'int4':
        kernel_h, kernel_w, _, num_filter, wmma_n, wmma_k  = get_const_tuple(Filter.shape)
    else:
        kernel_h, kernel_w, _, num_filter = get_const_tuple(Filter.shape)

    if in_dtype == 'int4':
        pass
        # assert (batch % 8 == 0 and in_channels % 32 == 0 and num_filter % 8 == 0)
    else:
        assert (batch % 16 == 0 and in_channels % 16 == 0 and num_filter % 16 == 0) or \
               (batch % 8 == 0 and in_channels % 16 == 0 and num_filter % 32 == 0) or \
               (batch % 32 == 0 and in_channels % 16 == 0 and num_filter % 8 == 0), \
               "The shape of (batch, in_channels, num_filter) "\
               "must be multiple of (16, 16, 16) or (32, 16, 8) or (8, 16, 32) for fp16 and int8, "\
               "and (8, 32, 8) for int4"

    # compute the output shape
    dilated_kernel_h = (kernel_h - 1) * dilation_h + 1
    dilated_kernel_w = (kernel_w - 1) * dilation_w + 1
    pad_top, pad_left, pad_down, pad_right = get_pad_tuple(
        padding, (dilated_kernel_h, dilated_kernel_w))
    out_channels = num_filter
    out_height = simplify((in_height - dilated_kernel_h + pad_top + pad_down) // stride_h + 1)
    out_width = simplify((in_width - dilated_kernel_w + pad_left + pad_right) // stride_w + 1)
    pad_before = [0, pad_top, pad_left, 0]
    pad_after = [0, pad_down, pad_right, 0]
    # PaddedInput = pad(Input, pad_before, pad_after, name="PaddedInput")
    # Input feature map: (N, H, W, IC, n, ic)
    data_shape = (batch,
                in_height,
                in_width,
                in_channels,
                wmma_m,
                wmma_k)
    # Kernel: (H, W, IC, OC, ic, oc)
    kernel_shape = (kernel_h,
                    kernel_w,
                    in_channels,
                    out_channels,
                    wmma_n,
                    wmma_k)
    output_shape = (batch,
                    out_height,
                    out_width,
                    out_channels,
                    wmma_m,
                    wmma_n)   
    # rc = te.reduce_axis((0, in_channel), name='rc')
    # ry = te.reduce_axis((0, kernel_h), name='ry')
    # rx = te.reduce_axis((0, kernel_w), name='rx')
    # Reduction axes
    kh = te.reduce_axis((0, kernel_h), name='kh')
    kw = te.reduce_axis((0, kernel_w), name='kw')
    ic = te.reduce_axis((0, in_channels), name='ic')
    ii = te.reduce_axis((0, wmma_k), name='ii')
    # Algorithm
    # A = te.placeholder(data_shape, name='A', dtype="int4")
    # W = te.placeholder(kernel_shape, name='W', dtype="int4")
    Apad = te.compute(
        (batch, in_height + 2 * padding, in_width + 2 * padding, in_channels, wmma_m,
        wmma_k),
        lambda n, h, w, i, nn, ii: tvm.tir.if_then_else(
            tvm.tir.all(h >= padding, h - padding < in_height,
                    w >= padding, w - padding < in_width),
            Input[n, h - padding, w - padding, i, nn, ii], tvm.tir.const(0., "int4")),
        name='Apad')
    Conv = te.compute(output_shape,
                    lambda n, h, w, o, nn, oo: te.sum(
                        Apad[n, h * stride_h + kh, w * stride_w + kw, ic, nn, ii].astype("int32") *
                        Filter[kh, kw, ic, o, oo, ii].astype("int32"),
                        axis=[ic, kh, kw, ii]),
                    name="Conv", tag="conv2d_nhwc_tensorcore_int4")

    return Conv


def schedule_nhwc_tensorcore_cuda_int4(cfg, s, Conv):
    """Schedule tensorcore template"""
    ic, kh, kw, ii = s[Conv].op.reduce_axis
    out_dtype = Conv.dtype
    # trans_paddata, kernel = s[Conv].op.input_tensors
    Apad, kernel = s[Conv].op.input_tensors
    s[Apad].compute_inline()
    in_dtype = Apad.dtype
    batch, _, _, _, _, _ = get_const_tuple(Conv.shape)
    if in_dtype == 'int4':
        _, _, out_channels, _, _, _  = get_const_tuple(kernel.shape)
    else:
        _, _, _, out_channels, _, _ = get_const_tuple(kernel.shape)
    # inline the pad and dtype transform
    # s[kernel].compute_inline()
    # s[paddata[0]].compute_inline()

    block_x = te.thread_axis('blockIdx.x')
    block_y = te.thread_axis('blockIdx.y')
    block_z = te.thread_axis('blockIdx.z')
    thread_x = te.thread_axis('threadIdx.x')
    thread_y = te.thread_axis('threadIdx.y')
    thread_z = te.thread_axis('threadIdx.z')

    # Designate the memory hierarchy
    AS = s.cache_read(Apad, 'shared', [Conv])
    WS = s.cache_read(kernel, 'shared', [Conv])
    AF = s.cache_read(AS, 'wmma.matrix_a', [Conv])
    WF = s.cache_read(WS, 'wmma.matrix_b', [Conv])
    ConvF = s.cache_write(Conv, 'wmma.accumulator')

    # todo 
    # if Conv.op in s.outputs:
    #     output = Conv
    #     ConvS = s.cache_read(ConvF, 'shared', [Conv])
    #     OL = ConvS
    # else:
    #     output = s.outputs[0].output(0)
    #     s[Conv].set_scope('shared')
    #     OL = Conv

    # Schedule for autotvm
    cfg.define_knob("block_row_warps", [1, 2, 4, 8])
    cfg.define_knob("block_col_warps", [1, 2, 4, 8])
    cfg.define_knob("warp_row_tiles", [1, 2, 4, 8])
    cfg.define_knob("warp_col_tiles", [1, 2, 4, 8])
    cfg.define_knob("chunk", [1, 2, 4, 8])
    # if in_dtype == 'int8':
    #     cfg.define_knob("offset", [0, 16])
    # elif in_dtype == 'int4':
    #     cfg.define_knob("offset", [0])
    # else:
    #     cfg.define_knob("offset", [0, 8])
    # cfg.define_knob("vector_width", [1, 2, 4, 8])
    cfg.define_knob("vector_width", [1, 8])

    # fallback support
    target = tvm.target.Target.current()
    if cfg.is_fallback:
        ref_log = autotvm.tophub.load_reference_log(
            target.target_name, target.model, 'conv2d_nhwc_tensorcore_int4.cuda')
        cfg.fallback_with_reference_log(ref_log)

    block_row_warps = cfg["block_row_warps"].val
    block_col_warps = cfg["block_col_warps"].val
    warp_row_tiles = cfg["warp_row_tiles"].val
    warp_col_tiles = cfg["warp_col_tiles"].val
    chunk = cfg["chunk"].val
    # offset = cfg["offset"].val
    vector_width = cfg["vector_width"].val
    block_row_warps = 1
    block_col_warps = 8
    warp_row_tiles = 2
    warp_col_tiles = 1
    chunk = 4
    vector_width = 1

    # offset = 0

    if in_dtype == 'int4':
        wmma_m = wmma_n = 8
        wmma_k = 32
    else:
        if (batch % 16 == 0 and out_channels % 16 == 0):
            cfg.define_knob("wmma_m", [16, 8, 32])
        elif (batch % 8 == 0 and out_channels % 32 == 0):
            cfg.define_knob("wmma_m", [8, 16, 32])
        elif (batch % 32 == 0 and out_channels % 8 == 0):
            cfg.define_knob("wmma_m", [32, 16, 8])
        wmma_m = cfg["wmma_m"].val
        # wmma_m = 16
        wmma_k = 16
        if wmma_m == 16:
            wmma_n = 16
        elif wmma_m == 8:
            wmma_n = 32
        elif wmma_m == 32:
            wmma_n = 8

    warp_size = 32

    nc, hc, wc, oc, nnc, ooc = Conv.op.axis
    block_k = s[Conv].fuse(hc, wc)
    s[Conv].bind(block_k, block_z)
    nc, nci = s[Conv].split(nc, factor=warp_row_tiles)
    block_i, nc = s[Conv].split(nc, factor=block_row_warps)
    oc, oci = s[Conv].split(oc, factor=warp_col_tiles)
    block_j, oc = s[Conv].split(oc, factor=block_col_warps)
    s[Conv].reorder(block_k, block_i, block_j, nc, oc, nci, oci, nnc, ooc)
    s[Conv].bind(block_i, block_x)
    s[Conv].bind(block_j, block_y)
    s[Conv].bind(nc, thread_y)
    s[Conv].bind(oc, thread_z)
    # Schedule local computation
    s[ConvF].compute_at(s[Conv], oc)
    n, h, w, o, nnf, oof = ConvF.op.axis
    ko, ki = s[ConvF].split(ic, factor=chunk)
    s[ConvF].reorder(ko, kh, ki, kw, n, o, nnf, oof, ii)

    # Move intermediate computation into each output compute tile
    s[AF].compute_at(s[ConvF], kw)
    s[WF].compute_at(s[ConvF], kw)

    # vector_width=8
    # Schedule for A's share memory
    s[AS].compute_at(s[ConvF], kh)
    n, h, w, i, nn, ii = AS.op.axis
    tx, xo = s[AS].split(n, nparts=block_row_warps)
    ty, yo = s[AS].split(xo, nparts=block_col_warps)
    t = s[AS].fuse(nn, ii)
    to, ti = s[AS].split(t, factor=warp_size)
    # ti, _t = s[AS].split(ti, factor=vector_width)
    s[AS].bind(tx, thread_y)
    s[AS].bind(ty, thread_z)
    s[AS].bind(ti, thread_x)
    # s[AS].vectorize(ti)

    # Schedule for W's share memory
    s[WS].compute_at(s[ConvF], kh)
    kh, kw, ic, o, ii, oo = WS.op.axis
    tx, xo = s[WS].split(o, nparts=block_row_warps)
    ty, yo = s[WS].split(xo, nparts=block_col_warps)
    t = s[WS].fuse(ii, oo)
    to, ti = s[WS].split(t, nparts=warp_size)
    ti, _t = s[WS].split(ti, factor=vector_width)
    s[WS].bind(tx, thread_y)
    s[WS].bind(ty, thread_z)
    s[WS].bind(to, thread_x)
    s[WS].vectorize(ti)

    s[AF].tensorize(AF.op.axis[-2], intrin_wmma_load_matrix('wmma.matrix_a'))
    s[WF].tensorize(WF.op.axis[-2], intrin_wmma_load_matrix('wmma.matrix_b'))
    s[Conv].tensorize(nnc, intrin_wmma_store_matrix())
    s[ConvF].tensorize(nnf, intrin_wmma_gemm())


    N, OH, OW, CO, nn, mm = get_const_tuple(Conv.shape)
    if in_dtype == 'int4':
        KH, KW, _, CI, _, ci = get_const_tuple(kernel.shape)
    else:
        KH, KW, CI, _ = get_const_tuple(kernel.shape)
    cfg.add_flop(2 * N * OH * OW * CO * CI * KH * KW * ci * nn * mm)


@autotvm.register_topi_compute("conv2d_nhwc_tensorcore_int4.cuda")
def conv2d_nhwc_tensorcore_int4(cfg, data, kernel, strides, padding, dilation, in_dtype, out_dtype):
    """Compute conv2d with tensorcore for NCHW layout"""
    return nhwc_tensorcore_cuda(cfg, data, kernel, strides, padding, dilation, in_dtype, out_dtype)


@autotvm.register_topi_schedule("conv2d_nhwc_tensorcore_int4.cuda")
def schedule_conv2d_nhwc_tensorcore_int4(cfg, outs):
    """TOPI schedule callback"""
    s = te.create_schedule([x.op for x in outs])
    def _callback(op):
        if 'conv2d_nhwc_tensorcore_int4' in op.tag:
            schedule_nhwc_tensorcore_cuda_int4(cfg, s, op.output(0))

    traverse_inline(s, outs[0].op, _callback)
    return s

