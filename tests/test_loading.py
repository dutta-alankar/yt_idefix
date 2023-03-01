import os
import sys
from pathlib import Path

import pytest

from yt_idefix.api import PlutoVtkDataset, PlutoXdmfDataset

HERE = Path(__file__).parent
DATADIR = HERE / "data"


ds_path_plutoVTK = DATADIR.joinpath("pluto_sod", "data.0001.vtk")
ds_path_plutoXDMF = DATADIR.joinpath("pluto_isentropic_vortex", "data.0010.flt.h5")


def test_load_from_str():
    PlutoVtkDataset(str(ds_path_plutoVTK))
    PlutoXdmfDataset(str(ds_path_plutoXDMF))


def test_load_from_path():
    PlutoVtkDataset(ds_path_plutoVTK)
    PlutoXdmfDataset(str(ds_path_plutoXDMF))


def test_load_from_parent_str():
    # https://github.com/neutrinoceros/yt_idefix/issues/88
    os.chdir(ds_path_plutoVTK.parent)
    fn = os.path.join("..", ds_path_plutoVTK.parent.name, ds_path_plutoVTK.name)
    PlutoVtkDataset(fn)
    os.chdir(ds_path_plutoXDMF.parent)
    fn = os.path.join("..", ds_path_plutoXDMF.parent.name, ds_path_plutoXDMF.name)
    PlutoXdmfDataset(fn)


@pytest.mark.skipif(
    sys.version_info < (3, 9)
    or not ds_path_plutoVTK.is_relative_to(Path.home())
    or not ds_path_plutoXDMF.is_relative_to(Path.home()),
    reason=(
        "$HOME isn't a parent to the test dataset, "
        "or Python is too old (< 3.9) for us to test that condition easily."
    ),
    # see https://docs.python.org/3/library/pathlib.html#pathlib.PurePath.is_relative_to
)
def test_load_from_home_str():
    # https://github.com/neutrinoceros/yt_idefix/issues/91
    fn = os.path.join("~", ds_path_plutoVTK.relative_to(Path.home()))
    PlutoVtkDataset(fn)
    fn = os.path.join("~", ds_path_plutoXDMF.relative_to(Path.home()))
    PlutoXdmfDataset(fn)
