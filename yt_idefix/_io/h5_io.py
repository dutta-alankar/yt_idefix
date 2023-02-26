from __future__ import annotations

import os
import struct
import warnings
from typing import Any, BinaryIO, Literal, overload

import numpy as np

from yt.utilities.on_demand_imports import _h5py as h5py

from .commons import Coordinates, Shape, mapFromCart

KNOWN_GEOMETRIES: dict[int, str] = {
    0: "cartesian",
    1: "polar",
    2: "spherical",
    3: "cylindrical",
}


def read_header(filename: str) -> str:
    with open(filename, "rb") as fh:
        return "".join(fh.readline(256).decode() for _ in range(2))


@overload
def read_single_field(
    fh: BinaryIO,
    *,
    shape: tuple[int, int, int],
    offset: int | None = None,
    skip_data: Literal[False],
) -> np.ndarray:
    ...


@overload
def read_single_field(
    fh: BinaryIO,
    *,
    shape: tuple[int, int, int],
    offset: int | None = None,
    skip_data: Literal[True],
) -> None:
    ...


def read_single_field(
    fh,
    *,
    shape,
    offset=None,
    skip_data=False,
):
    count = np.prod(shape)
    if offset is not None and fh.tell() != offset:
        fh.seek(offset)
    if skip_data:
        fh.seek(count * np.dtype("f").itemsize, 1)
        data = None
    else:
        data = np.fromfile(fh, ">f", count=count)
        data.shape = shape[::-1]
        data = data.T
    return data


def read_shape(s: str) -> Shape:
    # read a specific line containing nx, ny, nz

    assert s.startswith("DIMENSIONS")
    raw: list[str] = s.split()[1:]
    if len(raw) != 3:
        raise RuntimeError
    return Shape(*(int(_) for _ in raw))


def parse_shape(s: str, md: dict[str, Any]) -> None:
    md["shape"] = read_shape(s)


# this may not be kept in the following form
def read_metadata(fh: BinaryIO) -> dict[str, Any]:
    fh.seek(0)
    # skip over the first 4 lines which normally contains
    # VTK DataFile Version x.x
    # <Comments>
    # BINARY
    # DATASET RECTILINEAR_GRID or STRUCTURED_GRID
    for _ in range(4):
        next(fh)

    metadata: dict[str, Any] = {}
    line = next(fh).decode()  # DIMENSIONS NX NY NZ or FIELD
    if line.startswith("FIELD"):
        # Idefix >= 0.8
        nfield = int(line.split()[2])
        for _ in range(nfield):
            d = next(fh).decode()
            if d.startswith("GEOMETRY"):
                geom_flag: int = struct.unpack(">i", fh.read(4))[0]
                geometry_from_data = KNOWN_GEOMETRIES.get(geom_flag)
                if geometry_from_data is None:
                    warnings.warn(
                        f"Unknown geometry enum value {geom_flag}, please report this."
                    )
                metadata["geometry"] = geometry_from_data
            elif d.startswith("TIME"):
                metadata["time"] = struct.unpack(">f", fh.read(4))[0]
            elif d.startswith("PERIODICITY"):
                metadata["periodicity"] = tuple(
                    np.fromfile(fh, dtype=">i4", count=3).astype(bool)
                )
            else:
                warnings.warn(f"Found unknown field {d!r}")
            next(fh)  # skip extra linefeed (empty line)
        parse_shape(next(fh).decode(), metadata)

    elif line.startswith("DIMENSIONS"):
        parse_shape(line, metadata)

    else:
        raise RuntimeError(f"Failed to parse {line!r}")

    return metadata


def read_grid_coordinates(
    filename: str | os.PathLike[str],
    *,
    geometry: str | None = None,
) -> Coordinates:
    # Return cell edges coordinates
    fh = h5py.File(filename, "r")
    if geometry not in (valid_geometries := tuple(KNOWN_GEOMETRIES.values())):
        raise ValueError(
            f"Got unknown geometry {geometry!r}, expected one of {valid_geometries}"
        )

    nodesX = fh["/node_coords/X"].astype("=f8")
    nodesY = fh["/node_coords/Y"].astype("=f8")
    nodesZ = fh["/node_coords/Z"].astype("=f8")
    shape = Shape(
        *np.array(nodesX).shape
    )  # this is reversed compared the vtk implementation in vtk_io.py
    coords: list[np.ndarray] = []
    # now assuming that fh is positioned at the end of metadata
    if geometry in ("cartesian", "cylindrical"):
        pointsX = nodesX[0, 0, :]
        pointsY = nodesY[0, :, 0]
        pointsZ = nodesZ[:, 0, 0]
        coords = [pointsX, pointsY, pointsZ]
    else:
        assert geometry in ("polar", "spherical")

        dimensions = len(np.array(nodesX).shape)
        if dimensions == 1:
            nodesX = np.array(
                [
                    [
                        nodesX,
                    ],
                ]
            )
            nodesY = np.array(
                [
                    [
                        nodesY,
                    ],
                ]
            )
            nodesZ = np.array(
                [
                    [
                        nodesZ,
                    ],
                ]
            )
            array_shape = Shape(*shape).to_cell_centered()
        elif dimensions == 2:
            nodesX = np.array(
                [
                    nodesX,
                ]
            )
            nodesY = np.array(
                [
                    nodesY,
                ]
            )
            nodesZ = np.array(
                [
                    nodesZ,
                ]
            )
            array_shape = Shape(*shape).to_cell_centered()
        else:
            array_shape = Shape(*reversed(shape)).to_cell_centered()
        ordering = (2, 1, 0)

        xcart = np.transpose(nodesX, ordering)
        ycart = np.transpose(nodesY, ordering)
        zcart = np.transpose(nodesZ, ordering)

        coords = mapFromCart(xcart, ycart, zcart, geometry)
    fh.close()
    return Coordinates(coords[0], coords[1], coords[2], array_shape)


def read_field_offset_index(fh: BinaryIO, shape: Shape) -> dict[str, int]:
    # assuming fh is correctly positioned (read_grid_coordinates must be called first)
    retv: dict[str, int] = {}

    while True:
        line = fh.readline()
        if len(line) < 2:
            break
        s = line.decode()
        datatype, varname, dtype = s.split()

        # some versions of Pluto define field names in lower case
        # so we normalize to upper case to avoid duplicating data
        # in IdefixVtkFieldInfo.known_other_fields
        varname = varname.upper()

        if datatype == "SCALARS":
            next(fh)
            retv[varname] = fh.tell()
            read_single_field(fh, shape=shape, skip_data=True)
        elif datatype == "VECTORS":
            for axis in "XYZ":
                vname = f"{varname}_{axis}"
                retv[vname] = fh.tell()
                read_single_field(fh, shape=shape, skip_data=True)
        else:
            raise RuntimeError(f"Unknown datatype {datatype!r}")
        fh.readline()
    return retv
