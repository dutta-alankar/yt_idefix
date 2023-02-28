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
    dimensions = len(np.array(nodesX).shape)
    shape = Shape(
        *np.array(nodesX).shape
    )  # this is reversed compared the vtk implementation in vtk_io.py
    coords: list[np.ndarray] = []
    # now assuming that fh is positioned at the end of metadata
    if geometry in ("cartesian", "cylindrical") or (
        geometry in ("polar", "spherical") and dimensions == 1
    ):
        if dimensions == 1:
            nodesX = np.array(nodesX)
            nodesY = np.array(
                [0.0, 1.0]
            )  # Default is assumed and not parsed from pluto.ini/grid.out (not present in data)
            nodesZ = np.array([0.0, 1.0])
            if geometry == "spherical":
                if np.fabs(np.max(nodesX) - np.min(nodesX)) < 1e-8:
                    nodesX = fh["/cell_coords/X"].astype("=f8")
                    nodesX = np.hstack(([nodesX[1] - nodesX[0]], np.array(nodesX)))
                    nodesX = np.array(nodesX) / np.sin(0.5)
            elif geometry == "polar":
                if np.fabs(np.max(nodesX) - np.min(nodesX)) < 1e-8:
                    nodesX = fh["/cell_coords/X"].astype("=f8")
                    nodesX = np.hstack(([nodesX[1] - nodesX[0]], np.array(nodesX)))
                    nodesX = np.array(nodesX) / np.cos(0.5)
            array_shape = Shape(shape[0], 1, 1).to_cell_centered()
        elif dimensions == 2:
            nodesX = np.array(nodesX[0, :])
            nodesY = np.array(nodesY[:, 0])
            if geometry == "cartesian":
                nodesZ = np.array([0.0, 1.0])
            else:
                nodesZ = np.array([0.0, 2 * np.pi])
            array_shape = Shape((*reversed(shape[:-1]), 1)).to_cell_centered()
        else:
            nodesX = np.array(nodesX)[0, 0, :]
            nodesY = np.array(nodesY)[0, :, 0]
            nodesZ = np.array(nodesZ)[:, 0, 0]
            array_shape = Shape(*reversed(shape)).to_cell_centered()
        coords = [nodesX, nodesY, nodesZ]
    elif geometry in ("polar", "spherical") and dimensions > 1:
        if dimensions == 2:
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
            array_shape = Shape((*reversed(shape[:-1]), 1)).to_cell_centered()
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
