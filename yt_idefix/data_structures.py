from __future__ import annotations

import logging
import os
import re
import warnings
import weakref
from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import inifix
import numpy as np

from yt.data_objects.index_subobjects.stretched_grid import StretchedGrid
from yt.data_objects.static_output import Dataset
from yt.funcs import setdefaultattr
from yt.geometry.grid_geometry_handler import GridIndex
from yt.utilities.lib.misc_utilities import (  # type: ignore [import]
    _obtain_coords_and_widths,
)
from yt.utilities.on_demand_imports import _h5py as h5py
from yt_idefix._typing import UnitLike

from ._io import C_io, dmp_io, vtk_io
from ._io.commons import IdefixFieldProperties, IdefixMetadata
from .definitions import _PlutoBaseUnits, pluto_def_constants
from .fields import (
    BaseVtkFields,
    IdefixDmpFields,
    IdefixVtkFields,
    PlutoVtkFields,
    PlutoXdmfFields,
)

# import IO classes to ensure they are properly registered,
# even though we don't call them directly
from .io import IdefixDmpIO, IdefixVtkIO, PlutoVtkIO  # noqa

if TYPE_CHECKING:
    # these should really be unyt_array,
    # but mypy doesn't recognize it as a valid type as of unyt 2.9.3 and mypy 0.991
    XSpans = np.ndarray
    YSpans = np.ndarray
    ZSpans = np.ndarray
    XCoords = np.ndarray
    YCoords = np.ndarray
    ZCoords = np.ndarray

ytLogger = logging.getLogger("yt")
_DEF_GEOMETRY_REGEXP: Final = re.compile(r"^\s*#define\s+GEOMETRY\s+([A-Z]+)")
_DEF_UNIT_REGEXP: Final = re.compile(r"^\s*#define\s+UNIT_(\w+)\s+(\S+)")


class IdefixGrid(StretchedGrid):
    _id_offset = 0

    def __init__(self, id, cell_widths, filename, index, level, dims):
        super().__init__(id=id, filename=filename, index=index, cell_widths=cell_widths)
        self.Parent = None
        self.Children = []
        self.Level = level
        self.ActiveDimensions = dims


class IdefixHierarchy(GridIndex, ABC):
    grid = IdefixGrid

    def __init__(self, ds, dataset_type="idefix"):
        self.dataset_type = dataset_type
        self.dataset = weakref.proxy(ds)
        # for now, the index file is the dataset!
        self.index_filename = self.dataset.parameter_filename
        self.directory = os.path.dirname(self.index_filename)
        # float type for the simulation edges and must be float64 now
        self.float_type = np.float64
        super().__init__(ds, dataset_type)

    def _detect_output_fields(self):
        self.field_list = [
            (self.dataset_type, f) for f in self.dataset._detected_field_list
        ]

    def _count_grids(self):
        self.num_grids = 1

    def _parse_index(self):
        self.grid_left_edge[0][:] = self.ds.domain_left_edge[:]
        self.grid_right_edge[0][:] = self.ds.domain_right_edge[:]
        self.grid_dimensions[0][:] = self.ds.domain_dimensions[:]
        self.grid_particle_count[0][0] = 0

        # Idefix/Pluto are not AMR
        self.grid_levels[0][0] = 0
        self.min_level = self.max_level = 0

        self._field_offsets = self._get_field_offset_index()

    def _populate_grid_objects(self):
        # the minimal form of this method is
        #
        # for g in self.grids:
        #     g._prepare_grid()
        #     g._setup_dx()
        #
        # This must also set:
        #   g.Children <= list of child grids
        #   g.Parent   <= parent grid
        # This is handled by the frontend because often the children must be identified.
        self.grids = np.empty(self.num_grids, dtype="object")

        assert self.num_grids == 1

        i = 0
        g = self.grid(
            id=i,
            index=self,
            filename=self.index_filename,
            cell_widths=self._cell_widths,
            level=self.grid_levels.flat[i],
            dims=self.grid_dimensions[i],
        )
        g._prepare_grid()
        g._setup_dx()
        self.grids[i] = g

    @abstractmethod
    def _get_field_offset_index(self) -> dict[str, int]:
        HEADER_SIZE: int = 256
        with open(self.index_filename, "rb") as fh:
            fh.seek(HEADER_SIZE)
            field_index = ...
        return field_index  # type: ignore

    @abstractmethod
    @cached_property
    def _cell_widths(self) -> tuple[XSpans, YSpans, ZSpans]:
        # must return a 3-tuple of 1D unyt_array
        # with unit "code_length" and dtype float64
        ...

    @abstractmethod
    @cached_property
    def _cell_centers(self) -> tuple[XCoords, YCoords, ZCoords]:
        # must return a 3-tuple of 1D unyt_array
        # with unit "code_length" and dtype float64
        ...

    def _icoords_to_fcoords(
        self,
        icoords: np.ndarray,
        ires: np.ndarray,
        axes: tuple[int, ...] | None = None,
    ):
        if axes is None:
            axes = (0, 1, 2)
        # this is needed to support projections
        coords = np.empty(icoords.shape, dtype="f8")
        cell_widths = np.empty(icoords.shape, dtype="f8")
        for i, ax in enumerate(axes):
            coords[:, i], cell_widths[:, i] = _obtain_coords_and_widths(
                icoords[:, i],
                ires,
                self._cell_widths[ax],
                self.ds.domain_left_edge[ax].d,
            )
        return coords, cell_widths


class IdefixVtkHierarchy(IdefixHierarchy):
    def _get_field_offset_index(self) -> dict[str, int]:
        return self.ds._field_offset_index

    @cached_property
    def _cell_widths(self) -> tuple[XSpans, YSpans, ZSpans]:
        with open(self.index_filename, "rb") as fh:
            cell_edges = vtk_io.read_grid_coordinates(fh, geometry=self.ds.geometry)

        dims = self.ds.domain_dimensions
        length_unit = self.ds.quan(1, "code_length")

        cell_widths: tuple[XSpans, YSpans, ZSpans]
        cell_widths = (
            np.empty(max(dims[0], 2), dtype="float64") * length_unit,
            np.empty(max(dims[1], 2), dtype="float64") * length_unit,
            np.empty(max(dims[2], 2), dtype="float64") * length_unit,
        )

        for idir, edges in enumerate(cell_edges[:3]):
            if dims[idir] > 1:
                cell_widths[idir][:] = np.ediff1d(edges)
            else:
                cell_widths[idir][:] = self.ds.domain_width[idir]
        return cell_widths

    @cached_property
    def _cell_centers(self) -> tuple[XCoords, YCoords, ZCoords]:
        with open(self.index_filename, "rb") as fh:
            cell_edges = vtk_io.read_grid_coordinates(fh, geometry=self.ds.geometry)

        dims = self.ds.domain_dimensions
        length_unit = self.ds.quan(1, "code_length")

        cell_centers: tuple[XCoords, YCoords, ZCoords]
        cell_centers = (
            np.empty(max(dims[0], 2), dtype="float64") * length_unit,
            np.empty(max(dims[1], 2), dtype="float64") * length_unit,
            np.empty(max(dims[2], 2), dtype="float64") * length_unit,
        )

        for idir, edges in enumerate(cell_edges[:3]):
            if dims[idir] > 1:
                cell_centers[idir][:] = 0.5 * (edges[1:] + edges[:-1])
            else:
                cell_centers[idir][:] = edges[0]
        return cell_centers


class IdefixDmpHierarchy(IdefixHierarchy):
    def _get_field_offset_index(self) -> dict[str, int]:
        with open(self.index_filename, "rb") as fh:
            return dmp_io.get_field_offset_index(fh)

    @cached_property
    def _cell_widths(self) -> tuple[XSpans, YSpans, ZSpans]:
        _fprops, fdata = dmp_io.read_idefix_dmpfile(self.index_filename, skip_data=True)
        length_unit = self.ds.quan(1, "code_length")
        return (
            (fdata["xr1"] - fdata["xl1"]) * length_unit,
            (fdata["xr2"] - fdata["xl2"]) * length_unit,
            (fdata["xr3"] - fdata["xl3"]) * length_unit,
        )

    @cached_property
    def _cell_centers(self) -> tuple[XCoords, YCoords, ZCoords]:
        _fprops, fdata = dmp_io.read_idefix_dmpfile(self.index_filename, skip_data=True)
        length_unit = self.ds.quan(1, "code_length")
        return (
            fdata["x1"] * length_unit,
            fdata["x2"] * length_unit,
            fdata["x3"] * length_unit,
        )


class PlutoXdmfHierarchy(IdefixHierarchy):
    def _get_field_offset_index(self) -> dict[str, int]:
        # TODO(clm): refactor class hierarchy so this unused method can be removed
        return {}

    def _detect_output_fields(self):
        with h5py.File(self.index_filename, mode="r") as h5f:
            root = list(h5f.keys())[0]
            self.field_list = [
                (self.ds._dataset_type, str(k)) for k in list(h5f[f"{root}/vars/"])
            ]

    def _parse_grid_data(self, gridtxt):
        """
        In grid.out
        # ***********
        makrs the beginning and the end of header information
        """
        count = 0
        for _start, line in enumerate(gridtxt):
            if "# ***********" in line:
                count += 1
            if count == 2:
                break
        _start += 1  # This is the first line after the header lines in grid.out
        """
        grid.out data has the following format
        NX1
        <count> <left-edge> <right-edge>
          ...    ...          ... (NX1 rows)
        NX2
        <count> <left-edge> <right-edge>
          ...    ...          ... (NX2 rows)
        NX3
        <count> <left-edge> <right-edge>
          ...    ...          ... (NX3 rows)
        """
        # Beginning of the grid data
        start = _start
        nx1 = int(gridtxt[_start][:-1])
        start += nx1 + 1
        nx2 = int(gridtxt[start][:-1])
        start += nx2 + 1
        nx3 = int(gridtxt[start][:-1])

        # Seek back to the beginning of the grid data
        start = _start + 1
        cell_width1 = np.array(
            [
                float(gridtxt[i].split()[-1]) - float(gridtxt[i].split()[-2])
                for i in range(start, start + nx1)
            ]
        )
        start += nx1 + 1
        cell_width2 = np.array(
            [
                float(gridtxt[i].split()[-1]) - float(gridtxt[i].split()[-2])
                for i in range(start, start + nx2)
            ]
        )
        start += nx2 + 1
        cell_width3 = np.array(
            [
                float(gridtxt[i].split()[-1]) - float(gridtxt[i].split()[-2])
                for i in range(start, start + nx3)
            ]
        )
        cell_widths = (cell_width1, cell_width2, cell_width3)
        return cell_widths

    @cached_property
    def _cell_widths(self) -> tuple[XSpans, YSpans, ZSpans]:
        grid_file = os.path.join(self.directory, "grid.out")
        with open(grid_file) as gridtxt:
            txt = gridtxt.readlines()
            cell_widths = self._parse_grid_data(txt)
        return cell_widths

    @cached_property
    def _cell_centers(self) -> tuple[XCoords, YCoords, ZCoords]:
        cell_center1, cell_center2, cell_center3 = self._cell_widths
        return (
            cell_center1[0] + 0.5 * cell_center1,
            cell_center2[0] + 0.5 * cell_center2,
            cell_center3[0] + 0.5 * cell_center3,
        )


class IdefixDataset(Dataset, ABC):
    """A common abstraction for IdefixDmpDataset and IdefixVtkDataset."""

    _version_regexp = re.compile(r"v\d+\.\d+\.?\d*[-\w+]*")
    _dataset_type: str  # defined in subclasses
    _default_definitions_header = "definitions.hpp"
    _default_inifile = "idefix.ini"

    def __init__(
        self,
        filename,
        *,
        dataset_type: str | None = None,  # deleguated to child classes
        units_override: dict[str, UnitLike] | None = None,
        unit_system: Literal["cgs", "mks", "code"] = "cgs",
        default_species_fields: Literal["neutral", "ionized"] | None = None,
        # from here, frontend-specific arguments
        geometry: Literal["cartesian", "spherical", "cylindrical", "polar"]
        | None = None,
        inifile: str | os.PathLike[str] | None = None,
        definitions_header: str | os.PathLike[str] | None = None,
    ):
        self._geometry_from_user = geometry

        dt = type(self)._dataset_type
        self.fluid_types += (dt,)

        self._input_filename: str = os.fspath(filename)
        self._inifile = self._get_meta_file(inifile, default=self._default_inifile)
        self._definitions_header = self._get_meta_file(
            definitions_header, default=self._default_definitions_header
        )

        super().__init__(
            filename,
            dataset_type=dt,
            units_override=units_override,
            unit_system=unit_system,
            default_species_fields=default_species_fields,
        )
        self.storage_filename = None

        # idefix does not support grid refinement
        self.refine_by = 1

    def _get_meta_file(
        self, arg: str | os.PathLike[str] | None, /, *, default: str
    ) -> str:
        root_dir = Path(self.directory)

        if arg is not None:
            if os.path.isabs(arg):
                return os.fspath(arg)
            else:
                return str((root_dir / arg).absolute())

        _, ext = os.path.splitext(default)
        if (
            len(candidates := list(root_dir.glob(f"*{ext}"))) == 1
            and (file := candidates[0]).name == default
        ):
            return str(file.absolute())
        else:
            return ""

    def _parse_parameter_file(self):
        # base method, intended to be subclassed
        # parse the version hash
        self.parameters["code version"] = self._get_code_version()

        # idefix is never cosmological
        self.cosmological_simulation = 0
        self.current_redshift = 0.0
        self.omega_lambda = 0.0
        self.omega_matter = 0.0
        self.hubble_constant = 0.0

        self._parse_inifile()
        self._parse_definitions_header()
        self._setup_geometry()

    @staticmethod
    def _parse_geometry(geom: str):
        import yt

        if yt.version_info[:2] > (4, 1):
            try:
                from yt.geometry.api import Geometry  # type: ignore [attr-defined]

                return Geometry(geom)
            except ImportError:
                pass

        return geom

    def _setup_geometry(self) -> None:
        from_bin = self.parameters.get("geometry", "")
        from_definitions = self.parameters["definitions"].get("geometry", "")
        from_input = self._geometry_from_user or ""

        if from_definitions and from_bin and from_bin != from_definitions:
            raise RuntimeError(
                "Geometries from disk file and definitions header do not match, got\n"
                f" - {from_bin!r} (from {self.parameter_filename})\n"
                f" - {from_definitions!r} (from {self._definitions_header})"
            )

        from_disk = from_bin or from_definitions

        if not any((from_disk, from_input)):
            raise ValueError(
                "Geometry couldn't be parsed from disk. "
                "The 'geometry' keyword argument must be specified."
            )

        if from_input:
            if from_disk and from_input and from_input != from_disk:
                warnings.warn(
                    "Geometries from disk and input do not match, got\n"
                    f" - {from_disk!r} (from disk)\n"
                    f" - {from_input!r} (from input)\n"
                    "input prevails to allow working around hypothetical parsing bugs, "
                    "but it is very likely to result in an error in the general case."
                )
            geom_str = from_input
        else:
            assert from_disk
            geom_str = from_disk

        self.geometry = self._parse_geometry(geom_str)

    def _parse_inifile(self) -> None:
        if not self._inifile:
            return

        with open(self._inifile, "rb") as fh:
            self.parameters.update(inifix.load(fh))

    def _parse_definitions_header(self) -> None:
        self.parameters["definitions"] = {}
        if not self._definitions_header:
            return

        with open(self._definitions_header) as fh:
            body = fh.read()
        lines = C_io.strip_comments(body).split("\n")

        for line in lines:
            if (geom_match := re.fullmatch(_DEF_GEOMETRY_REGEXP, line)) is not None:
                self.parameters["definitions"]["geometry"] = geom_match.group(1).lower()
                return

    def _set_code_unit_attributes(self):
        # This is where quantities are created that represent the various
        # on-disk units.  These are the currently available quantities which
        # should be set, along with examples of how to set them to standard
        # values.
        #
        # self.length_unit = self.quan(1.0, "cm")
        # self.mass_unit = self.quan(1.0, "g")
        # self.time_unit = self.quan(1.0, "s")
        # self.time_unit = self.quan(1.0, "s")
        #
        # These can also be set:
        # self.velocity_unit = self.quan(1.0, "cm/s")
        # self.magnetic_unit = self.quan(1.0, "gauss")
        for key, unit in self.__class__.default_units.items():
            setdefaultattr(self, key, self.quan(1, unit))

    # The following methods are frontend-specific

    @abstractmethod
    def _read_data_header(self) -> str:
        pass

    def _get_code_version(self) -> str:
        # take the last line of the header
        # - in Idefix dumps there's only one line
        # - in Vtk files (Idefix or Pluto), there are two,
        #   the first of which isn't code specific
        header = self._read_data_header().splitlines()[-1]

        regexp = self.__class__._version_regexp

        match = re.search(regexp, header)
        if match is None:
            warnings.warn(
                f"Could not determine code version from file header {header!r}"
            )
            return "unknown"

        return match.group()


class IdefixVtkDataset(IdefixDataset):
    _index_class = IdefixVtkHierarchy
    _field_info_class: type[BaseVtkFields] = IdefixVtkFields
    _dataset_type = "idefix-vtk"
    _required_header_keyword = "Idefix"

    @classmethod
    def _is_valid(cls, filename, *args, **kwargs) -> bool:
        try:
            header = vtk_io.read_header(filename)
        except Exception:
            return False
        else:
            return cls._required_header_keyword in header

    def _read_data_header(self) -> str:
        return vtk_io.read_header(self.parameter_filename)

    def _parse_parameter_file(self):
        # parse metadata
        with open(self.parameter_filename, "rb") as fh:
            md = vtk_io.read_metadata(fh)
        self.parameters.update(md)

        super()._parse_parameter_file()
        # from here self.geometry is assumed to be set

        # parse the grid
        with open(self.parameter_filename, "rb") as fh:
            coords = vtk_io.read_grid_coordinates(fh, geometry=self.geometry)
            self._field_offset_index = vtk_io.read_field_offset_index(
                fh, coords.array_shape
            )
        self._detected_field_list = list(self._field_offset_index.keys())

        self.domain_dimensions = np.array(coords.array_shape)
        self.dimensionality = np.count_nonzero(self.domain_dimensions - 1)

        dle = np.array([arr.min() for arr in coords.arrays], dtype="float64")
        dre = np.array([arr.max() for arr in coords.arrays], dtype="float64")

        # temporary hack to prevent 0-width dimensions for 2D data
        dre = np.where(dre == dle, dle + 1, dre)
        self.domain_left_edge = dle
        self.domain_right_edge = dre

        # time wasn't stored in vtk files before Idefix 0.8
        self.current_time = md.get("time", -1)

        # periodicity was not stored in vtk files before Idefix 0.9
        self._periodicity = md.get("periodicity", (True, True, True))


class IdefixDmpDataset(IdefixDataset):
    _index_class = IdefixDmpHierarchy
    _field_info_class = IdefixDmpFields
    _dataset_type = "idefix-dmp"

    @classmethod
    def _is_valid(cls, filename, *args, **kwargs) -> bool:
        try:
            header_string = dmp_io.read_header(filename)
            return re.match(r"Idefix .* Dump Data", header_string) is not None
        except Exception:
            return False

    def _get_fields_metadata(self) -> tuple[IdefixFieldProperties, IdefixMetadata]:
        # read everything except large arrays
        return dmp_io.read_idefix_dmpfile(self.parameter_filename, skip_data=True)

    def _read_data_header(self) -> str:
        return dmp_io.read_header(self.parameter_filename)

    def _parse_parameter_file(self):
        fprops, fdata = self._get_fields_metadata()
        self.parameters.update(fdata)

        self._detected_field_list = [k for k in fprops if re.match(r"^V[sc]-", k)]

        # parse the grid
        axes = ("x1", "x2", "x3")
        self.domain_dimensions = np.concatenate([fprops[k][-1] for k in axes])
        self.dimensionality = np.count_nonzero(self.domain_dimensions - 1)

        # note that domain edges parsing is already implemented in a mutli-block
        # supporting fashion even though we specifically error out in case there's more
        # than one block.
        self.domain_left_edge = np.array(
            [fdata[f"xl{idir}"][0] for idir in "123"], dtype="float64"
        )
        self.domain_right_edge = np.array(
            [fdata[f"xr{idir}"][-1] for idir in "123"], dtype="float64"
        )

        self.current_time = fdata["time"]

        self._periodicity = tuple(bool(p) for p in fdata["periodicity"])

        super()._parse_parameter_file()


class PlutoVtkDataset(IdefixVtkDataset):
    _field_info_class = PlutoVtkFields
    _dataset_type = "pluto-vtk"
    _version_regexp = re.compile(r"\d+\.\d+\.?\d*[-\w+]*")
    _required_header_keyword = "PLUTO"
    _default_definitions_header = "definitions.h"
    _default_inifile = "pluto.ini"

    def _parse_parameter_file(self):
        super()._parse_parameter_file()

        # parse time from vtk.out
        log_file = os.path.join(self.directory, "vtk.out")
        if (match := re.search(r"\.(\d*)\.", self.parameter_filename)) is None:
            raise RuntimeError(
                f"Failed to parse output number from file name {self.parameter_filename}"
            )
        index = int(match.group(1))

        # will be converted to actual unyt_quantity in _set_derived_attrs
        self.current_time = -1

        if not os.path.isfile(log_file):
            ytLogger.warning("Missing log file %s, setting current_time = -1", log_file)
            return

        log_regexp = re.compile(rf"^{index}\s(\S+)")
        with open(log_file) as fh:
            for line in fh.readlines():
                log_match = re.search(log_regexp, line)
                if log_match:
                    self.current_time = float(log_match.group(1))
                    break
            else:
                ytLogger.warning(
                    "Failed to retrieve time from %s, setting current_time = -1",
                    log_file,
                )

    def _parse_definitions_header(self) -> None:
        """Read some metadata from header file 'definitions.h'."""
        self.parameters["definitions"] = {}
        if not self._definitions_header:
            ytLogger.warning(
                "%s was not found. Code units will be set to 1.0 in cgs.",
                self._default_definitions_header,
            )
            return

        with open(self._definitions_header) as fh:
            body = fh.read()
        lines = C_io.strip_comments(body).split("\n")

        for line in lines:
            if (geom_match := re.fullmatch(_DEF_GEOMETRY_REGEXP, line)) is not None:
                self.parameters["definitions"]["geometry"] = geom_match.group(1).lower()
            elif (unit_match := re.fullmatch(_DEF_UNIT_REGEXP, line)) is not None:
                unit = unit_match.group(1).lower() + "_unit"
                expr = unit_match.group(2)
                # Before evaluating the expression, replace the input parameters,
                # pre-defined constants, code units and sqrt function
                # that cannot be resolved. The order doesn't matter.
                expr = re.sub(r"g_inputParam\[(\w+)\]", self._get_input_parameter, expr)
                expr = re.sub(r"CONST_\w+", self._get_constants, expr)
                expr = re.sub(r"UNIT_(\w+)", self._get_unit, expr)
                expr = re.sub(r"sqrt", "np.sqrt", expr)
                self.parameters["definitions"][unit] = eval(expr)

    def _get_input_parameter(self, match: re.Match) -> str:
        """Replace matched input parameters with its value"""
        key = match.group(1)
        return str(self.parameters["Parameters"][key])

    def _get_unit(self, match: re.Match) -> str:
        """Replace matched unit with its value"""
        key = match.group(1).lower() + "_unit"
        return str(self.parameters["definitions"].get(key, 1.0))

    def _get_constants(self, match: re.Match) -> str:
        """Replace matched constant string with its value"""
        key = match.group()
        return str(pluto_def_constants[key])

    def _set_code_unit_attributes(self):
        """Conversion between physical units and code units."""

        # Pluto's base units are length, velocity and density, but here we consider
        # length, mass and time as base units. Since it can make us easy to calculate
        # all units when self.units_override is not None.

        # Default values of Pluto's base units which are stored in self.parameters
        # if they can be read from definitions.h
        # Otherwise, they are set to unity in cgs.
        defs = self.parameters["definitions"]
        pluto_units = {
            "velocity_unit": self.quan(defs.get("velocity_unit", 1.0), "cm/s"),
            "density_unit": self.quan(defs.get("density_unit", 1.0), "g/cm**3"),
            "length_unit": self.quan(defs.get("length_unit", 1.0), "cm"),
        }

        uo_size = len(self.units_override)
        if uo_size > 0 and uo_size < 3:
            ytLogger.info(
                "Less than 3 units were specified in units_override (got %s). "
                "Need to rely on PLUTO's internal units to derive other units",
                uo_size,
            )

        uo_cache = self.units_override.copy()
        while len(uo_cache) < 3:
            # If less than 3 units were passed into units_override,
            # the rest will be chosen from Pluto's units
            unit, value = pluto_units.popitem()
            # If any Pluto's base unit is specified in units_override, it'll be preserved
            if unit in uo_cache:
                continue
            uo_cache[unit] = value
            # Make sure the combination of units are able to derive base units
            # No need of validation and logging when no unit to be overrided
            if uo_size > 0:
                try:
                    self._validate_units_override_keys(uo_cache)
                except ValueError:
                    # It means the combination is invalid
                    del uo_cache[unit]
                else:
                    ytLogger.info("Relying on %s: %s.", unit, uo_cache[unit])

        bu = _PlutoBaseUnits(uo_cache)
        for unit, value in bu._data.items():
            setattr(self, unit, value)

        self.velocity_unit = self.length_unit / self.time_unit
        self.density_unit = self.mass_unit / self.length_unit**3
        self.magnetic_unit = (
            np.sqrt(4.0 * np.pi * self.density_unit) * self.velocity_unit
        )
        self.magnetic_unit.convert_to_units("gauss")
        self.temperature_unit = self.quan(1.0, "K")

    invalid_unit_combinations = [
        {"magnetic_unit", "velocity_unit", "density_unit"},
        {"velocity_unit", "time_unit", "length_unit"},
        {"density_unit", "length_unit", "mass_unit"},
    ]

    default_units = {
        "length_unit": "cm",
        "time_unit": "s",
        "mass_unit": "g",
        "velocity_unit": "cm/s",
        "magnetic_unit": "gauss",
        "temperature_unit": "K",
        # this is the one difference with Dataset.default_units:
        # we accept density_unit as a valid override
        "density_unit": "g/cm**3",
    }

    @classmethod
    def _validate_units_override_keys(cls, units_override):
        """Check that units in units_override are able to derive three base units:
        mass, length and time
        """

        # YT supports overriding other normalisations, this method ensures consistency
        # between supplied 'units_override' items and principles in PLUTO.

        # PLUTO's normalisations/units have 3 degrees of freedom. Therefore, any combinations
        # are valid other than the three cases listed explicitly.

        if "temperature_unit" in units_override:
            raise ValueError(
                "Temperature is not allowed in units_override, "
                "since it's always in Kelvin in PLUTO"
            )

        # Three units are enough for deriving others, more will likely cause conflict
        if len(units_override) > 3:
            raise ValueError(
                "More than 3 degrees of freedom were specified "
                f"in units_override ({len(units_override)} given)"
            )

        # check if provided overrides are allowed
        suo = set(units_override)
        if suo in cls.invalid_unit_combinations:
            raise ValueError(
                f"Combination {suo} passed to units_override "
                "cannot derive all units\n"
                f"Choose any other combinations, except for:\n {cls.invalid_unit_combinations}"
            )

        super(cls, cls)._validate_units_override_keys(units_override)


class PlutoXdmfDataset(PlutoVtkDataset):
    _index_class = PlutoXdmfHierarchy
    _field_info_class = PlutoXdmfFields
    _dataset_type = "pluto-xdmf"
    _required_header_keyword = "PLUTOXdmf"
    _field_offset_index = {}  # TODO(clm): clean this up

    def _parse_parameter_file(self):
        # IdefixDataset._parse_parameter_file()
        grid_file = os.path.join(self.directory, "grid.out")  # data-loc/grid.out
        (
            self.parameter_filename[:-2] + "xmf"
        )  # data.%04d.<dbl/flt>.h5 -> data.%04d.<dbl/flt>.xmf
        out_file = os.path.join(
            self.directory,
            self.parameter_filename[-6:]
            + ".out",  # data.%04d.<dbl/flt>.h5 -> <dbl/flt>.h5.out
        )
        self._default_inifile = os.path.join(self.directory, self._default_inifile)
        self._default_definitions_header = os.path.join(
            self.directory, self._default_definitions_header
        )
        if os.path.exists(self._default_definitions_header):
            with open(self._default_definitions_header) as deftxt:
                lines = deftxt.readlines()
                for line in lines:
                    if "USER_DEF_PARAMETERS" in line:
                        self.inputParamCount = int(line.split()[-1])

        """
        grid.out file has entries like the following:

            # DIMENSIONS: 2
            # GEOMETRY:   POLAR
            # X1: [ 0.040000,  0.500000], 400 point(s), 2 ghosts
            # X2: [ 0.000000,  6.283185], 400 point(s), 2 ghosts

        These lines need to be parsed to create the appropriate grid data structure in yt.
        Splitting and extracting the numers become straightforward when '[', ']' and ',' characters are removed before the split.
        """
        with open(grid_file) as gridtxt:
            txt = gridtxt.readlines()
            for line in txt:
                if "# DIMENSIONS" in line:
                    self.dimensionality = int(line.split()[-1])
                    break
            domain_left_edge = np.zeros(self.dimensionality, dtype=np.float64)
            domain_right_edge = np.zeros(self.dimensionality, dtype=np.float64)
            domain_dimensions = np.zeros(self.dimensionality, dtype=np.int64)
            geom_str = None
            count = 0

            for line in txt:
                if ("# X1" in line) or ("# X2" in line) or ("# X3" in line):
                    tmp = (
                        line.replace(",", "").replace("[", "").replace("]", "").split()
                    )
                    domain_left_edge[count] = float(tmp[2])
                    domain_right_edge[count] = float(tmp[3])
                    domain_dimensions[count] = int(tmp[4])
                    count += 1
                if "# GEOMETRY" in line:
                    geom_str = (line.split()[-1]).lower()
            for i in range(
                count, self.dimensionality
            ):  # These are dummy, may need some fix
                domain_left_edge[i] = 0.0
                domain_right_edge[i] = 1.0
                domain_dimensions[i] = 1
            self.domain_left_edge = domain_left_edge
            self.domain_right_edge = domain_right_edge
            self.domain_dimensions = domain_dimensions

            self.geometry = self._parse_geometry(geom_str)

        with open(out_file) as outttxt:
            txt = outttxt.readlines()
            """
            Filenames are data.<snapnum>.<dbl/flt>.h5
            <snapnum> needs to be parse from the filename.
            <snapnum> is the corresponding entry in the <dbl/flt>.h5.out file
            Example <dbl/flt>.h5.out file:
                0 0.000000e+00 1.000000e-04 0 single_file little rho vx1 vx2 vx3 prs tr1 tr2 tr3 Temp ndens PbykB mach
                1 2.498181e+00 3.500985e-03 747 single_file little rho vx1 vx2 vx3 prs tr1 tr2 tr3 Temp ndens PbykB mach
                2 4.998045e+00 3.400969e-03 1458 single_file little rho vx1 vx2 vx3 prs tr1 tr2 tr3 Temp ndens PbykB mach
                3 7.497932e+00 3.386245e-03 2186 single_file little rho vx1 vx2 vx3 prs tr1 tr2 tr3 Temp ndens PbykB mach
            """
            entry = int(
                os.path.basename(self.parameter_filename)
                .replace(".flt.h5", "")
                .replace("data.", "")
                .replace(".dbl.h5", "")
            )
            self.current_time = float(txt[entry].split()[1])
            self.ntracers = 0
            search = "tr1"  # Passive tracer names in PLUTO are tr1, tr2, tr3 and so on by default
            while search in txt[entry].split():
                self.ntracers += 1
                search = f"tr{self.ntracers + 1}"

        self.fluid_types += (self._dataset_type,)
        # check g_gamma value in the simulation and change this if needed. Unfortunately, automating this is not trivial.
        self.gamma = 5.0 / 3.0
        # This is a ballpark number for fully ionized plasma. For more accuracy, say to obtain temperature, let PLUTO dump this field as a user-defined field.
        self.mu = 0.61
        self.storage_filename = self.parameter_filename
        self._periodicity = (True, True, True)
        # PLUTO cannot yet be run as a cosmological simulation
        self.cosmological_simulation = 0
        self.current_redshift = 0.0
        self.omega_lambda = 0.0
        self.omega_matter = 0.0
        self.hubble_constant = 0.0

    def _set_code_unit_attributes(self):
        # This is where quantities are created that represent the various
        # on-disk units.  These are the defaults, but if they are listed
        # in the HDF5 attributes for a file, which is loaded first, then those are
        # used instead.
        #
        from .definitions import pluto_def_constants
        from .misc import remove_comments

        length_unit = None
        mass_unit = None
        velocity_unit = None
        density_unit = None
        magnetic_unit = None

        if os.path.exists(self._default_definitions_header):
            with open(self._default_definitions_header) as deftxt:
                lines = deftxt.readlines()
                for line in lines:
                    line = remove_comments(line)
                    if "UNIT_LENGTH" in line:
                        length_unit = line.split()[-1]
                    if "UNIT_DENSITY" in line:
                        density_unit = line.split()[-1]
                    if "UNIT_VELOCITY" in line:
                        velocity_unit = line.split()[-1]

            constant_names = list(pluto_def_constants.keys())
            # if definitions.h uses inputParamaeter then these values need to be read from pluto.ini
            g_inputParam = {}
            if (
                ("g_inputParam" in length_unit)
                or ("g_inputParam" in density_unit)
                or ("g_inputParam" in velocity_unit)
            ):
                if not (os.path.exists(self._default_inifile)):
                    raise RuntimeError(
                        "pluto.ini file is needed for unit conversion but is missing!"
                    )
                with open(self._default_inifile) as ini:
                    lines = ini.readlines()
                    for _pos, line in enumerate(lines):
                        if "[Parameters]" in line:
                            break
                    tmp = 0
                    while tmp == 0:
                        _pos += 1
                        tmp = len(lines[_pos].split())
                    for i in range(_pos, _pos + self.inputParamCount):
                        txt = lines[i].split()
                        g_inputParam[txt[0]] = float(txt[1])
            inputParam_names = list(g_inputParam.keys())

            if length_unit:
                try:
                    length_unit = float(length_unit)
                except ValueError:
                    for name in constant_names:
                        length_unit = length_unit.replace(
                            name, f'pluto_def_constants["{name}"]'
                        )
                    for name in inputParam_names:
                        length_unit = length_unit.replace(name, f'"{name}"')
                    length_unit = length_unit.replace("sqrt", "np.sqrt")
                    length_unit = length_unit.replace("log", "np.log")
                    length_unit = length_unit.replace("pow", "np.power")
                    length_unit = eval(length_unit)

            if density_unit:
                try:
                    density_unit = float(density_unit)
                except ValueError:
                    for name in constant_names:
                        density_unit = density_unit.replace(
                            name, f'pluto_def_constants["{name}"]'
                        )
                    for name in inputParam_names:
                        density_unit = density_unit.replace(name, f'"{name}"')
                    density_unit = density_unit.replace("sqrt", "np.sqrt")
                    density_unit = density_unit.replace("log", "np.log")
                    density_unit = density_unit.replace("pow", "np.power")
                    density_unit = eval(density_unit)

            if velocity_unit:
                try:
                    velocity_unit = float(velocity_unit)
                except ValueError:
                    for name in constant_names:
                        velocity_unit = velocity_unit.replace(
                            name, f'pluto_def_constants["{name}"]'
                        )
                    for name in inputParam_names:
                        velocity_unit = velocity_unit.replace(name, f'"{name}"')
                    velocity_unit = velocity_unit.replace("sqrt", "np.sqrt")
                    velocity_unit = velocity_unit.replace("log", "np.log")
                    velocity_unit = velocity_unit.replace("pow", "np.power")
                    velocity_unit = eval(velocity_unit)

            if (density_unit) and (length_unit):
                mass_unit = density_unit * length_unit**3

        if not length_unit:
            self.length_unit = self.quan(1.0, "AU")
        else:
            self.length_unit = self.quan(length_unit, "cm")
        if not mass_unit:
            if not density_unit:
                mp = pluto_def_constants["CONST_mp"]
                self.mass_unit = self.quan(
                    self.quan(mp, "g/cm**3")
                    * self.length_unit**3
                    / self.quan(1.0, "Msun"),
                    "Msun",
                )
            else:
                self.mass_unit = self.quan(
                    self.quan(density_unit, "g/cm**3")
                    * self.length_unit**3
                    / self.quan(1.0, "Msun"),
                    "Msun",
                )
        else:
            self.mass_unit = self.quan(mass_unit, "g")
        if not velocity_unit:
            self.velocity_unit = self.quan(1.0, "km/s")
            self.time_unit = self.length_unit / self.velocity_unit
        else:
            self.velocity_unit = self.quan(velocity_unit, "cm/s")
            self.time_unit = self.length_unit / self.velocity_unit
        if not magnetic_unit:
            magnetic_unit = self.quan(1.0, "gauss")
        else:
            self.magnetic_unit = self.quan(magnetic_unit, "gauss")

        for key, unit in self.__class__.default_units.items():
            setdefaultattr(self, key, self.quan(1, unit))

    @classmethod
    def _is_valid(cls, filename, *args, **kwargs):
        # This accepts a filename or a set of arguments and returns True or
        # False depending on if the file is of the type requested.
        test = filename.endswith("dbl.h5") or filename.endswith("flt.h5")
        if not(test): return False
        test = os.path.exists(
            filename[:-2] + "xmf"
        )  # data.%04d.<dbl/flt>.h5 -> data.%04d.<dbl/flt>.xmf
        if not(test): return False
        test = os.path.exists(
            os.path.join(os.path.dirname(filename), "grid.out")
        )  # data-loc/grid.out
        if not(test): return False
        test = os.path.exists(
            os.path.join(os.path.dirname(filename), filename[-6:] + ".out")
        )  # data.%04d.<dbl/flt>.h5 -> <dbl/flt>.h5.out
        if not(test): return False
        try:
            fileh = h5py.File(filename, mode="r")
        except (ImportError, OSError):
            return False
        else:
            entries = list(fileh.keys())
            fileh.close()
            return (
                "cell_coords" in entries
                and "node_coords" in entries
            )
