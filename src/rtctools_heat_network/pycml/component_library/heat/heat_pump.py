from numpy import nan

from rtctools_heat_network.pycml import Variable
from rtctools_heat_network.pycml.component_library.heat.heat_four_port import HeatFourPort


class HeatPump(HeatFourPort):
    def __init__(self, name, **modifiers):
        super().__init__(
            name,
            **self.merge_modifiers(
                dict(),
                modifiers,
            ),
        )

        self.component_type = "heat_pump"
        self.COP = nan
        self.nominal = (
            self.Secondary.Q_nominal * self.Secondary.rho * self.Secondary.cp * self.Secondary.dT
        )

        # Assumption: heat in/out and added is nonnegative

        self.add_variable(Variable, "Primary_heat", min=0.0)
        self.add_variable(Variable, "Secondary_heat", min=0.0)
        self.add_variable(Variable, "Power_elec", min=0.0)
        self.add_variable(Variable, "dH_prim")
        self.add_variable(Variable, "dH_sec")

        # Hydraulically decoupled so Heads remain the same
        self.add_equation(self.dH_prim - (self.Primary.HeatOut.H - self.Primary.HeatIn.H))
        self.add_equation(self.dH_sec - (self.Secondary.HeatOut.H - self.Secondary.HeatIn.H))

        self.add_equation(
            ((self.Primary_heat + self.Power_elec - self.Secondary_heat) / self.nominal)
        )

        self.add_equation(((self.Secondary_heat - self.COP * self.Power_elec) / self.nominal))

        self.add_equation(
            (
                (self.Primary_heat - (self.Primary.HeatIn.Heat - self.Primary.HeatOut.Heat))
                / self.nominal
            )
        )
        self.add_equation(
            (
                (self.Secondary_heat - (self.Secondary.HeatOut.Heat - self.Secondary.HeatIn.Heat))
                / self.nominal
            )
        )
