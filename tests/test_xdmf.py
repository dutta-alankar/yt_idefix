import yt
from yt_idefix.api import PlutoXdmfDataset

# A sample list of units for test.
# The first three values are chosen randomly
# and others are calculated correspondingly.
SAMPLE_UNITS = {
    "time_unit": (2.0, "s"),
    "length_unit": (4.0, "cm"),
    "mass_unit": (5.0, "kg"),
    "density_unit": (0.078125, "kg/cm**3"),
    "velocity_unit": (2.0, "cm/s"),
    "magnetic_unit": (62.66570686577499, "gauss"),
}


def test_class_validation(xdmf_file):
    file = xdmf_file
    cls = {
        "plutoXDMF": PlutoXdmfDataset,
    }[file["kind"]]
    assert cls._is_valid(file["path"])


# TODO: make this a pytest-mpl test
def test_slice_plot(xdmf_file):
    file = xdmf_file
    ds = yt.load(file["path"], geometry=file["geometry"], unit_system="code")
    yt.SlicePlot(ds, normal=(0, 0, 1), fields=("gas", "density"))


def test_projection_plot(xdmf_file):
    file = xdmf_file
    ds = yt.load(file["path"], geometry=file["geometry"], unit_system="code")
    yt.ProjectionPlot(ds, normal=(0, 0, 1), fields=("gas", "density"))


def test_load_magic(xdmf_file):
    ds = yt.load(xdmf_file["path"], geometry=xdmf_file["geometry"])
    assert isinstance(ds, PlutoXdmfDataset)
