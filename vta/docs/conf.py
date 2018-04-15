# -*- coding: utf-8 -*-
#
# documentation build configuration file, created by
# sphinx-quickstart on Thu Jul 23 19:40:08 2015.
#
# This file is execfile()d with the current directory set to its
# containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.
import sys
import os, subprocess
import shlex
import recommonmark
import sphinx_gallery
from tvm.contrib import rpc, graph_runtime
from recommonmark.parser import CommonMarkParser
from recommonmark.transform import AutoStructify

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
curr_path = os.path.dirname(os.path.abspath(os.path.expanduser(__file__)))
sys.path.insert(0, os.path.join(curr_path, '../python/'))

# -- General configuration ------------------------------------------------

# General information about the project.
project = u'vta'
author = u'%s developers' % project
copyright = u'2018, %s' % author
github_doc_root = 'https://github.com/uwsaml/vta/tree/master/docs/'

# add markdown parser
CommonMarkParser.github_doc_root = github_doc_root
source_parsers = {
    '.md': CommonMarkParser
}
os.environ['VTA_BUILD_DOC'] = '1'
# Version information.
import vta
version = vta.__version__
release = vta.__version__

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom ones
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.intersphinx',
    'sphinx.ext.napoleon',
    'sphinx.ext.mathjax',
    'sphinx_gallery.gen_gallery',
]


breathe_projects = {'vta' : 'doxygen/xml/'}
breathe_default_project = 'vta'

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
# source_suffix = ['.rst', '.md']
source_suffix = ['.rst', '.md']

# The encoding of source files.
#source_encoding = 'utf-8-sig'

# generate autosummary even if no references
autosummary_generate = True

# The master toctree document.
master_doc = 'index'

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = None

# There are two options for replacing |today|: either, you set today to some
# non-false value, then it is used:
#today = ''
# Else, today_fmt is used as the format for a strftime call.
#today_fmt = '%B %d, %Y'

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
exclude_patterns = ['_build']

# The reST default role (used for this markup: `text`) to use for all
# documents.
#default_role = None

# If true, '()' will be appended to :func: etc. cross-reference text.
#add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
#add_module_names = True

# If true, sectionauthor and moduleauthor directives will be shown in the
# output. They are ignored by default.
#show_authors = False

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'sphinx'

# A list of ignored prefixes for module index sorting.
#modindex_common_prefix = []

# If true, keep warnings as "system message" paragraphs in the built documents.
#keep_warnings = False

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = False

# -- Options for HTML output ----------------------------------------------

# The theme is set by the make target
html_theme = os.environ.get('VTA_THEME', 'rtd')

on_rtd = os.environ.get('READTHEDOCS', None) == 'True'
# only import rtd theme and set it if want to build docs locally
if not on_rtd and html_theme == 'rtd':
    import sphinx_rtd_theme
    html_theme = 'sphinx_rtd_theme'
    html_theme_path = [sphinx_rtd_theme.get_html_theme_path()]

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

# Output file base name for HTML help builder.
htmlhelp_basename = project + 'doc'

# -- Options for LaTeX output ---------------------------------------------
latex_elements = {
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
  (master_doc, '%s.tex' % project, project,
   author, 'manual'),
]

# hook for doxygen
def run_doxygen(folder):
    """Run the doxygen make command in the designated folder."""
    try:
        retcode = subprocess.call("cd %s; make doc" % folder, shell=True)
        retcode = subprocess.call("rm -rf _build/html/doxygen", shell=True)
        retcode = subprocess.call("mkdir -p _build/html", shell=True)
        retcode = subprocess.call("cp -rf doxygen/html _build/html/doxygen", shell=True)
        if retcode < 0:
            sys.stderr.write("doxygen terminated by signal %s" % (-retcode))
    except OSError as e:
        sys.stderr.write("doxygen execution failed: %s" % e)

intersphinx_mapping = {
    'python': ('https://docs.python.org/{.major}'.format(sys.version_info), None),
    'numpy': ('http://docs.scipy.org/doc/numpy/', None),
    'scipy': ('http://docs.scipy.org/doc/scipy/reference', None),
    'matplotlib': ('http://matplotlib.org/', None),
    'tvm': ('http://docs.tvmlang.org/', None),
}

from sphinx_gallery.sorting import ExplicitOrder

examples_dirs = ['../tutorials/']
gallery_dirs = ['tutorials']
subsection_order = ExplicitOrder([])

def generate_doxygen_xml(app):
    """Run the doxygen make commands if we're on the ReadTheDocs server"""
    run_doxygen('..')

def setup(app):
    # Add hook for building doxygen xml when needed
    # no c++ API for now
    app.connect("builder-inited", generate_doxygen_xml)
    app.add_stylesheet('css/tvm_theme.css')
    app.add_config_value('recommonmark_config', {
        'url_resolver': lambda url: github_doc_root + url,
        'auto_doc_ref': True
            }, True)
    app.add_transform(AutoStructify)


sphinx_gallery_conf = {
    'backreferences_dir': 'gen_modules/backreferences',
    'doc_module': ('vta', 'numpy'),
'reference_url': {
    'vta': None,
    'tvm': 'http://docs.tvmlang.org',
    'matplotlib': 'http://matplotlib.org',
    'numpy': 'http://docs.scipy.org/doc/numpy-1.9.1'},
    'examples_dirs': examples_dirs,
    'gallery_dirs': gallery_dirs,
    'subsection_order': subsection_order,
    'find_mayavi_figures': False,
    'filename_pattern': '.py',
    'expected_failing_examples': []
}
