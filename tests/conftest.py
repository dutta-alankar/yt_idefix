from __future__ import annotations

import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
import unyt as un
import yaml
from packaging.version import Version

UNYT_VERSION = Version(version("unyt"))
NUMPY_VERSION = Version(version("numpy"))


def pytest_configure(config):
    if sys.version_info >= (3, 10) and NUMPY_VERSION < Version("1.23"):
        config.addinivalue_line(
            "filterwarnings",
            (
                "ignore:The distutils.sysconfig module is deprecated, use sysconfig instead:DeprecationWarning"
            ),
        )


DATA_DIR = Path(__file__).parent / "data"

VTK_FILES: dict[str, dict[str, Any]] = {}
XDMF_FILES: dict[str, dict[str, Any]] = {}


def load_meta(pdir, meta_file):
    with open(meta_file) as fh:
        metadata = yaml.load(fh, yaml.SafeLoader)

    metadata["attrs"]["path"] = pdir / metadata["attrs"]["path"]
    if "units" in metadata["attrs"]:
        keys = list(metadata["attrs"]["units"].keys())
        for u in keys:
            metadata["attrs"]["units"][u] = un.unyt_quantity.from_string(
                metadata["attrs"]["units"][u]
            )
    return metadata


for ddir in os.listdir(DATA_DIR):
    pdir = DATA_DIR / ddir
    for filename in os.listdir(pdir):
        if filename.startswith("meta") and filename.endswith(".yaml"):
            meta_file = pdir / filename
            if not pdir.is_dir():
                continue
            if not meta_file.is_file():
                continue
            metadata = load_meta(pdir, meta_file)
            if "XDMF" in metadata["attrs"]["kind"]:
                XDMF_FILES.update({metadata["id"]: metadata["attrs"]})
            else:
                VTK_FILES.update({metadata["id"]: metadata["attrs"]})


@pytest.fixture(params=VTK_FILES.values(), ids=VTK_FILES.keys(), scope="session")
def vtk_file(request):
    return request.param


@pytest.fixture(params=XDMF_FILES.values(), ids=XDMF_FILES.keys(), scope="session")
def xdmf_file(request):
    return request.param


# useful subsets
VTK_FILES_NO_GEOMETRY = {
    k: v for k, v in VTK_FILES.items() if v["has_geometry"] is False
}

XDMF_FILES_NO_GEOMETRY = {
    k: v for k, v in XDMF_FILES.items() if v["has_geometry"] is False
}


@pytest.fixture(
    params=VTK_FILES_NO_GEOMETRY.values(),
    ids=VTK_FILES_NO_GEOMETRY.keys(),
    scope="session",
)
def vtk_file_no_geom(request):
    return request.param


@pytest.fixture(
    params=XDMF_FILES_NO_GEOMETRY.values(),
    ids=XDMF_FILES_NO_GEOMETRY.keys(),
    scope="session",
)
def xdmf_file_no_geom(request):
    return request.param


VTK_FILES_WITH_GEOMETRY = {
    k: v for k, v in VTK_FILES.items() if v["has_geometry"] is True
}

XDMF_FILES_WITH_GEOMETRY = {
    k: v for k, v in XDMF_FILES.items() if v["has_geometry"] is True
}


@pytest.fixture(
    params=VTK_FILES_WITH_GEOMETRY.values(),
    ids=VTK_FILES_WITH_GEOMETRY.keys(),
    scope="session",
)
def vtk_file_with_geom(request):
    return request.param


@pytest.fixture(
    params=XDMF_FILES_WITH_GEOMETRY.values(),
    ids=XDMF_FILES_WITH_GEOMETRY.keys(),
    scope="session",
)
def xdmf_file_with_geom(request):
    return request.param


VTK_FILES_WITH_UNITS = {k: v for k, v in VTK_FILES.items() if v["has_units"] is True}

XDMF_FILES_WITH_UNITS = {k: v for k, v in XDMF_FILES.items() if v["has_units"] is True}


@pytest.fixture(
    params=VTK_FILES_WITH_UNITS.values(),
    ids=VTK_FILES_WITH_UNITS.keys(),
    scope="session",
)
def vtk_file_with_units(request):
    return request.param


@pytest.fixture(
    params=XDMF_FILES_WITH_UNITS.values(),
    ids=XDMF_FILES_WITH_UNITS.keys(),
    scope="session",
)
def xdmf_file_with_units(request):
    return request.param


IDEFIX_VTK_FILES = {k: v for k, v in VTK_FILES.items() if v["kind"] == "idefix"}


@pytest.fixture(
    params=IDEFIX_VTK_FILES.values(), ids=IDEFIX_VTK_FILES.keys(), scope="session"
)
def idefix_vtk_file(request):
    return request.param


PLUTO_VTK_FILES = {k: v for k, v in VTK_FILES.items() if v["kind"] == "plutoVTK"}
PLUTO_XDMF_FILES = {k: v for k, v in VTK_FILES.items() if v["kind"] == "plutoXDMF"}


@pytest.fixture(
    params=PLUTO_VTK_FILES.values(), ids=PLUTO_VTK_FILES.keys(), scope="session"
)
def pluto_vtk_file(request):
    return request.param


@pytest.fixture(
    params=PLUTO_XDMF_FILES.values(), ids=PLUTO_XDMF_FILES.keys(), scope="session"
)
def pluto_xdmf_file(request):
    return request.param
