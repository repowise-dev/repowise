"""PyPI distribution name -> the module names code imports it under.

A manifest declares a *distribution* (``Pillow``, ``djangorestframework``)
while code imports a *module* (``PIL``, ``rest_framework``). Most
distributions are imported under their own name, lowercased with ``-`` and
``.`` turned into ``_`` (``Django`` -> ``django``, ``typing-extensions`` ->
``typing_extensions``); the table below covers the well-known ones that are
not. A dotted value (``google.protobuf``) is matched as a module prefix.

The names are only ever offered as link candidates: a link forms solely when
a manifest declares the distribution, so an entry cannot link an import to
something the repository never named.

Deliberate ceiling: an installed environment's ``top_level.txt`` would name
every distribution's modules exactly, but the indexing machine is usually not
the project's environment (a clean clone has no virtualenv) and the
declarations are stored without it, so the table is the one source. Reading
an environment, when one sits in the repository, is the upgrade path.
"""

from __future__ import annotations

import re

#: PEP 503 distribution key -> import module names.
PYPI_IMPORT_NAMES: dict[str, tuple[str, ...]] = {
    "attrs": ("attr", "attrs"),
    "beautifulsoup4": ("bs4",),
    "django-cors-headers": ("corsheaders",),
    "django-debug-toolbar": ("debug_toolbar",),
    "django-filter": ("django_filters",),
    "django-mptt": ("mptt",),
    "django-taggit": ("taggit",),
    "django-timezone-field": ("timezone_field",),
    "djangorestframework": ("rest_framework",),
    "djangorestframework-simplejwt": ("rest_framework_simplejwt",),
    "dnspython": ("dns",),
    "elasticsearch-dsl": ("elasticsearch_dsl",),
    "faiss-cpu": ("faiss",),
    "gitpython": ("git",),
    "google-api-python-client": ("googleapiclient",),
    "grpcio": ("grpc",),
    "msgpack-python": ("msgpack",),
    "opencv-contrib-python": ("cv2",),
    "opencv-python": ("cv2",),
    "opencv-python-headless": ("cv2",),
    "pillow": ("PIL",),
    "protobuf": ("google.protobuf",),
    "psycopg2-binary": ("psycopg2",),
    "psycopg-binary": ("psycopg",),
    "pycryptodome": ("Crypto",),
    "pycryptodomex": ("Cryptodome",),
    "pyjwt": ("jwt",),
    "pymupdf": ("fitz", "pymupdf"),
    "pyopenssl": ("OpenSSL",),
    "pyserial": ("serial",),
    "python-dateutil": ("dateutil",),
    "python-docx": ("docx",),
    "python-dotenv": ("dotenv",),
    "python-jose": ("jose",),
    "python-magic": ("magic",),
    "python-multipart": ("multipart", "python_multipart"),
    "python-slugify": ("slugify",),
    "pyyaml": ("yaml",),
    "pyzmq": ("zmq",),
    "ruamel-yaml": ("ruamel.yaml",),
    "scikit-image": ("skimage",),
    "scikit-learn": ("sklearn",),
    "setuptools": ("setuptools", "pkg_resources"),
    "tensorflow-cpu": ("tensorflow",),
}


def pypi_dist_key(name: str) -> str:
    """PEP 503 spelling, so ``PyYAML`` and ``pyyaml`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).casefold()


def pypi_import_names(distribution: str) -> tuple[str, ...]:
    """Module names a distribution is imported under, most likely first."""
    known = PYPI_IMPORT_NAMES.get(pypi_dist_key(distribution))
    if known is not None:
        return known
    return (re.sub(r"[-.]+", "_", distribution).casefold(),)


__all__ = ["PYPI_IMPORT_NAMES", "pypi_dist_key", "pypi_import_names"]
