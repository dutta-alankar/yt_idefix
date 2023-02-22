
import numpy as np

from yt.fields.field_info_container import FieldInfoContainer
from yt.fields.magnetic_field import setup_magnetic_field_aliases
from yt.utilities.physical_constants import kboltz, mh


class BaseVtkFields(FieldInfoContainer):
    known_other_fields = (
        ("RHO", ("code_mass / code_length**3", ["density"], None)),  # type: ignore
        ("VX1", ("code_length / code_time", ["velocity_x"], None)),
        ("VX2", ("code_length / code_time", ["velocity_y"], None)),
        ("VX3", ("code_length / code_time", ["velocity_z"], None)),
        ("BX1", ("code_magnetic", [], None)),
        ("BX2", ("code_magnetic", [], None)),
        ("BX3", ("code_magnetic", [], None)),
        ("PRS", ("code_pressure", ["pressure"], None)),
    )


class IdefixVtkFields(BaseVtkFields):
    def setup_fluid_fields(self):
        setup_magnetic_field_aliases(
            self, "idefix-vtk", [f"BX{idir}" for idir in "123"]
        )


class PlutoVtkFields(BaseVtkFields):
    def setup_fluid_fields(self):
        setup_magnetic_field_aliases(self, "pluto-vtk", [f"BX{idir}" for idir in "123"])


class IdefixDmpFields(FieldInfoContainer):
    known_other_fields = (
        ("Vc-RHO", ("code_mass / code_length**3", ["density"], None)),  # type: ignore
        ("Vc-VX1", ("code_length / code_time", ["velocity_x"], None)),
        ("Vc-VX2", ("code_length / code_time", ["velocity_y"], None)),
        ("Vc-VX3", ("code_length / code_time", ["velocity_z"], None)),
        ("Vc-BX1", ("code_magnetic", [], None)),
        ("Vc-BX2", ("code_magnetic", [], None)),
        ("Vc-BX3", ("code_magnetic", [], None)),
        ("Vc-PRS", ("code_pressure", ["pressure"], None)),
    )
    # note that velocity '_x', '_y' and '_z' aliases are meant to be
    # overwriten according to geometry in self.setup_fluid_aliases

    known_particle_fields = ()

    def setup_fluid_fields(self):
        setup_magnetic_field_aliases(
            self, "idefix-dmp", [f"Vc-BX{idir}" for idir in "123"]
        )


class PlutoXdmfFields(FieldInfoContainer):
    _pres_units = "code_pressure"
    _erg_units = "code_mass * (code_length/code_time)**2"
    _rho_units = "code_mass / code_length**3"
    _mom_units = "code_mass / code_length**2 / code_time"
    _vel_units = "code_length/code_time"

    known_other_fields = (
        # Each entry here is of the form
        # ( "name", ("units", ["fields", "to", "alias"], # "display_name")),
        ("rho", (_rho_units, ["density", "rho"], None)),
        ("vx1", (_vel_units, ["vel_x"], None)),
        ("vx2", (_vel_units, ["vel_y"], None)),
        ("vx3", (_vel_units, ["vel_z"], None)),
        ("prs", (_pres_units, ["prs", "pres", "pressure"], None)),
    )

    known_particle_fields = ()

    def setup_fluid_fields(self):
        unit_system = self.ds.unit_system

        # Add tracer fields
        for i in range(1, self.ds.ntracers + 1):
            if (self.ds._dataset_type, "tr%d" % i) in self.field_list:
                self.add_output_field(
                    (self.ds._dataset_type, "tr%d" % i),
                    sampling_type="cell",
                    units="",
                )
                self.alias(
                    ("gas", "Tracer %d" % i),
                    (self.ds._dataset_type, "tr%d" % i),
                    units="",
                )
                self.alias(
                    ("gas", "tracer %d" % i),
                    (self.ds._dataset_type, "tr%d" % i),
                    units="",
                )

        if (self.ds._dataset_type, "Temp") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "Temp"),
                sampling_type="cell",
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "Temperature"),
                (self.ds._dataset_type, "Temp"),
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "temperature"),
                (self.ds._dataset_type, "Temp"),
                units=unit_system["temperature"],
            )
        elif (self.ds._dataset_type, "temp") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "temp"),
                sampling_type="cell",
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "Temperature"),
                (self.ds._dataset_type, "temp"),
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "temperature"),
                (self.ds._dataset_type, "temp"),
                units=unit_system["temperature"],
            )
        elif (self.ds._dataset_type, "Temperature") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "Temperature"),
                sampling_type="cell",
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "Temperature"),
                (self.ds._dataset_type, "Temperature"),
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "temperature"),
                (self.ds._dataset_type, "Temperature"),
                units=unit_system["temperature"],
            )
        elif (self.ds._dataset_type, "temperature") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "temperature"),
                sampling_type="cell",
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "Temperature"),
                (self.ds._dataset_type, "temperature"),
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "temperature"),
                (self.ds._dataset_type, "temperature"),
                units=unit_system["temperature"],
            )
        else:

            def _temperature(field, data):
                return (
                    data.ds.mu
                    * (mh / kboltz)
                    * (data[("gas", "pressure")] / data[("gas", "density")])
                )

            self.add_field(
                ("gas", "Temperature"),
                sampling_type="cell",
                function=_temperature,
                units=unit_system["temperature"],
            )
            self.alias(
                ("gas", "temperature"),
                ("gas", "Temperature"),
                units=unit_system["temperature"],
            )

        if (self.ds._dataset_type, "mach") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "mach"),
                sampling_type="cell",
                units="",
            )
            self.alias(
                ("gas", "Mach"),
                (self.ds._dataset_type, "mach"),
                units="",
            )
            self.alias(
                ("gas", "mach"),
                (self.ds._dataset_type, "mach"),
                units="",
            )
        else:

            def _mach(field, data):
                return np.sqrt(
                    data[(self.ds._dataset_type, "vx1")] ** 2
                    + data[(self.ds._dataset_type, "vx2")] ** 2
                    + data[(self.ds._dataset_type, "vx3")] ** 2
                ) / np.sqrt(
                    data.ds.gamma
                    * (data[("gas", "pressure")] / data[("gas", "density")])
                )

            self.add_field(
                ("gas", "Mach"),
                sampling_type="cell",
                function=_mach,
                units="",
            )
            self.alias(
                ("gas", "mach"),
                ("gas", "Mach"),
                units="",
            )

        if (self.ds._dataset_type, "ndens") in self.field_list:
            self.add_output_field(
                (self.ds._dataset_type, "ndens"),
                sampling_type="cell",
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "ndens"),
                (self.ds._dataset_type, "ndens"),
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "Number Density"),
                (self.ds._dataset_type, "ndens"),
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "number density"),
                (self.ds._dataset_type, "ndens"),
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "Number density"),
                (self.ds._dataset_type, "ndens"),
                units=unit_system["length"] ** -3,
            )
        else:

            def _ndens(field, data):
                return data["gas", "density"] / (data.ds.mu * mh)

            self.add_field(
                ("gas", "Number Density"),
                sampling_type="cell",
                function=_ndens,
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "number density"),
                ("gas", "Number Density"),
                units=unit_system["length"] ** -3,
            )
            self.alias(
                ("gas", "Number density"),
                ("gas", "Number Density"),
                units=unit_system["length"] ** -3,
            )

        def _velMag(field, data):
            print("PLUTO ", data)
            return np.sqrt(
                data[(self.ds._dataset_type, "vx1")] ** 2
                + data[(self.ds._dataset_type, "vx2")] ** 2
                + data[(self.ds._dataset_type, "vx3")] ** 2
            )

        self.add_field(
            ("gas", "Speed"),
            sampling_type="cell",
            function=_velMag,
            units=unit_system["velocity"],
        )
        self.alias(
            ("gas", "speed"),
            ("gas", "Speed"),
            units=unit_system["velocity"],
        )
        self.alias(
            ("gas", "Velocity Magnitude"),
            ("gas", "Speed"),
            units=unit_system["velocity"],
        )

    def setup_particle_fields(self, ptype):
        super().setup_particle_fields(ptype)
        # This will get called for every particle type.
