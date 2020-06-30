import numpy as np
import math
from datetime import datetime
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import logging
import time

from rtctools.optimization.collocated_integrated_optimization_problem \
    import CollocatedIntegratedOptimizationProblem
from rtctools.optimization.csv_mixin import CSVMixin
from rtctools.optimization.goal_programming_mixin \
    import Goal, GoalProgrammingMixin, StateGoal
from rtctools.optimization.homotopy_mixin import HomotopyMixin
from rtctools.optimization.modelica_mixin import ModelicaMixin
from rtctools.optimization.timeseries import Timeseries
from rtctools.util import run_optimization_problem

from DW_linearization_functions import friction_factor_plot, head_loss

logger = logging.getLogger("rtctools")


class RangeGoal(Goal):
    def __init__(self, optimization_problem, state, state_bounds, target_min, target_max, priority, order=2):
        self.state = state
        self.target_min = target_min
        self.target_max = target_max
        self.priority = priority
        self.order = order
        self.function_range = state_bounds
        self.function_nominal = abs((state_bounds[1] + state_bounds[0]) / 2.0)

    def function(self, optimization_problem, ensemble_member):
        return optimization_problem.state(self.state)


class MinimizeGoal(Goal):
    def __init__(self, optimization_problem, state, priority, function_nominal=1.0, order=1):
        self.state = state
        self.function_nominal = function_nominal
        self.priority = priority
        self.order = order

    def function(self, optimization_problem, ensemble_member):
        return optimization_problem.state(self.state)/self.function_nominal


class MaximizeGoal(Goal):
    def __init__(self, optimization_problem, state, priority, function_nominal=1.0, order=1):
        self.state = state
        self.function_nominal = function_nominal
        self.priority = priority
        self.order = order

    def function(self, optimization_problem, ensemble_member):
        return -optimization_problem.state(self.state)/self.function_nominal


class Example(HomotopyMixin, GoalProgrammingMixin, CSVMixin, ModelicaMixin,
              CollocatedIntegratedOptimizationProblem):

    # Create useful lists

    # List of pipe names
    pipes = [
        'pipe1aC',
        'pipe1bC',
        'pipe4aC',
        'pipe4bC',
        'pipe5C',
        'pipe7C',
        'pipe9C',
        'pipe15C',
        'pipe25C',
        'pipe26C',
        'pipe27C',
        'pipe29C',
        'pipe30C',
        'pipe31C',
        'pipe32C',
        'pipe52_inC',
        'pipe52_outC',
        'pipe1aH',
        'pipe1bH',
        'pipe4aH',
        'pipe4bH',
        'pipe5H',
        'pipe7H',
        'pipe9H',
        'pipe15H',
        'pipe25H',
        'pipe26H',
        'pipe27H',
        'pipe29H',
        'pipe30H',
        'pipe31H',
        'pipe32H',
        'pipe52_inH',
        'pipe52_outH'
        ]

    pipe_profile_hot = [
        'pipe1aH',
        'pipe1bH',
        'pipe5H',
        'pipe7H',
        'pipe9H',
        'pipe15H',
        'pipe25H',
        'pipe27H',
        'pipe29H',
        'pipe31H',
        'pipe32H',
        ]

    pipe_profile_cold = [
        'pipe32C',
        'pipe31C',
        'pipe29C',
        'pipe27C',
        'pipe25C',
        'pipe15C',
        'pipe9C',
        'pipe7C',
        'pipe5C',
        'pipe1bC',
        'pipe1aC',
    ]

    nodes = [
    'nodeS2H',
    'nodeD7H',
    'nodeD92H',
    'nodeB1H',
    'nodeS2C',
    'nodeD7C',
    'nodeD92C',
    'nodeB1C',
    ]

    # List of structures
    demands = [
        'demand7',
        'demand91',
        'demand92',
        ]

    sources = [
        'source1',
        'source2',
        ]

    buffers = [
        'buffer1'
        ]

    pumps = [
        'pump1',
        'pump2',
        ]


    def path_constraints(self, ensemble_member):
        constraints = super().path_constraints(0)
        # Path constraints are constraints that must be applied at every time step.
        # (Rtc-tools in the backend will collocate these constraints in time.)

        # A couple of things about rtc-tools conventions:
        # A constraint has the shape: ((f(x), lb, ub)) meaning that lb <= f(x) <= ub.
        # The naming of the variables is: name_of_components+.+(Q/H/Heat/dH etc.)
        # The naming of the components comes from the Modelica model Example.mo (in model folder).
        # The names of the variables come from the different component and Port modelica models. The variables are in Modelica SIUnits.
        # Say you want to constraint the heat of source1, the naming will then be 'source1.Heat'.
        # To set a constraint on a variable, one has to use: self.state(name_variable)

        # For this model, we have the following constraints:
        # * head loss relationship for pipes (e.g., dH >= cQ^2)
        # * head_loss relationship for sources
        # * pressure of at least 1 bar at the demand

        # Head loss in pipes
        # To model the relationship |dH| = cQ^2: impose the constraint |dH| >= cQ^2 and the optimize such that |dH| is dragged down.
        # (Note that in the model of a pipe dH = Out.H - In.H and thus dH <= 0.0. Thus in the optimization problem one needs to maximize dH.)
        for pipe in self.pipes:
            gravitational_constant = 9.81
            friction_factor = 0.04
            diameter = self.parameters(0)[pipe+'.diameter']
            length = self.parameters(0)[pipe+'.length']
            # Compute c_v constant (where |dH| ~ c_v*v^2)
            c_v = length * friction_factor / (2 * gravitational_constant) / diameter
            area = math.pi * diameter**2 / 4
            v = self.state(pipe+'.QTHOut.Q')/area
            constraints.append((
                -self.state(pipe+'.dH') - c_v*v**2, 0.0, np.inf
                ))

        # Head loss in sources
        for s in self.sources:
            c = self.parameters(0)[s+'.head_loss']
            if c == 0.0:
                constraints.append((
                    self.state(s+'.QTHIn.H') - self.state(s+'.QTHOut.H'), 0.0, 0.0
                    ))
            else:
                constraints.append((
                    self.state(s+'.QTHIn.H') - self.state(s+'.QTHOut.H') - c*self.state(s+'.QTHOut.Q')**2, 0.0, np.inf
                    ))

        # For each demand components the head loss is at least 1 bar
        for d in self.demands:
            constraints.append((
                self.state(d+'.QTHIn.H') - self.state(d+'.QTHOut.H'), 10.0, np.inf
                ))

        # As temperatures are variables, need to fix dT at demand nodes. Set dT to be exactly 30.0.
        for d in self.demands:
            constraints.append((
                self.state(d+'.QTHIn.T') - self.state(d+'.QTHOut.T'), 30.0, 30.0
                ))

        # In the linear model, fix the temperature in the pipes.
        # (I.e., supply and returns lines have respective temperatures 75 and 45.)
        if self.h_th == 0.0:
            for s in self.sources:
                constraints.append((
                    self.state(s+'.QTHIn.T') - self.parameters(0)[s+'.T_return'], 0.0, 0.0
                    ))
                constraints.append((
                    self.state(s+'.QTHOut.T') - self.parameters(0)[s+'.T_supply'], 0.0, 0.0
                    ))

        # Ensure that buffer does not extract heat from the return line. For this, we impose that the nonnegative temperature jump in the return line when it 'intersect' the buffer.
        constraints.append((
            self.state('pipe9C.QTHIn.T') - self.state('pipe15C.QTHOut.T'), 0.0, np.inf
            ))


        return constraints

    def constraints(self, ensemble_member):
        constraints = super().constraints(0)
        # Constraints are used to set a constraint in a particular timestep.
        # Needs the construction self.state_at(variable_name, timestep)

        # Amount of heat stored in the buffer at the beginning of the time horizon is set to 0.0
        t0 = self.times()[0]
        constraints.append((
            self.state_at('buffer1.Stored_heat', t0)/self.variable_nominal('buffer1.Stored_heat'), 0.0, 0.0
            ))

        return constraints


    def path_goals(self):
        goals = super().path_goals()
        # Similarly to path_constraints, path goals are goals that will be applied at every time step.

        # There are two types of goals in rtc-tools:
        # * set a minimum and(/or) maximum target
        # * minimize a certain function

        # You can see the goals classes in the beginning of this code.
        # RangeGoal: sets a minimum and/or a maximum target on a certain state.
        # One has to provide the state, the target_min and target_max (set np.nan if it doesn't apply),
        # state_bounds which are the physical lower and upper bounds to the variable, the priority of the goal
        # and (optionally) the order. Order=2 means that target violations will be punished quadratically. 
        # Order=1 means that violations are punished linearly.
        # (If you play around with the order of the goal at priority 3 you will see the effect kicking in.)

        # MaximizeGoal: maximizes the given state.

        # We have four different goals:
        # * priority 1: match the Heat demand
        # * priority 2: extract a certain (constant) heat from source1
        # * priority 3: minimize the usage of source2

        # Match the demand target heat
        for d in self.demands:
            k = d[6:]
            var = d+'.Heat'
            target_heat = self.get_timeseries('Heat_demand_'+k)
            # Note: with the self.get_timeseries function you can extract the timeseries that are
            # in the timeseries_import file in the input folder.
            # Timeseries objects have (times, values) as property.
            target_heat_val = target_heat.values

            target_heat_ts = Timeseries(self.times(), target_heat_val)
            lb = min(target_heat_ts.values)*0.9
            ub = max(target_heat_ts.values)*1.1
            goals.append(RangeGoal(self, state=var, target_min=target_heat_ts, target_max=target_heat_ts, state_bounds=(lb,ub), priority=1, order=1))

        # Extract certain heat from source1
        goals.append(RangeGoal(self, state='source1.Heat', target_min=1e5, target_max=1e5, state_bounds=(0.0, 1.5e6), priority=2, order=2))

        # Minimize the usage of source2
        goals.append(RangeGoal(self, state='source2.Heat', target_max=0.0, target_min=np.nan, state_bounds=(0.0, 1.5e6), priority=3, order=2))

        return goals

    # Store the homotopy parameter
    @property
    def h_th(self):
        return self.parameters(0)['theta']

    # Overwrite default solver options
    def solver_options(self):
        options = super().solver_options()
        solver = options['solver']
        options[solver]['nlp_scaling_method'] = 'none'
        options[solver]['linear_system_scaling'] = 'none'
        options[solver]['linear_scaling_on_demand'] = 'no'
        options[solver]['max_iter'] = 1000
        options[solver]['tol'] = 1e-5
        options[solver]['acceptable_tol'] = 1e-5
        return options

    def post(self):
        times = self.times()
        results = self.extract_results()

        # This function is called after the optimization run. Thus can be used to do analysis of the results, make plots etc.
        # To get the results of the optimization for a certain variable use: results[name_variable]


        ### RESULTS ANALYSIS ###

        # # TODO (Teresa): add post-processing step to compute dH.

        # # Check that for each pipe |dH| ~=c*Q^2 (i.e., check that results are physically feasible)
        # tol = 1e-4
        # for pipe in self.pipes:
        #     gravitational_constant = 9.81
        #     friction_factor = 0.04
        #     diameter = self.parameters(0)[pipe+'.diameter']
        #     length = self.parameters(0)[pipe+'.length']
        #     #calculate constant
        #     c = length * friction_factor / ((2 * gravitational_constant) * diameter)
        #     area = math.pi * diameter**2 / 4
        #     dH = results[pipe+'.dH']
        #     Q = results[pipe+'.Q']
        #     diff = -dH - c/area**2*Q**2
        #     if np.any(np.abs(diff) > tol):
        #         logger.error("dH != cQ^2 for pipe {} by {}".format(pipe[4:], max(np.abs(diff))))

        # # (Possibly) Useful info for debugging purposes
        # print('Pumps')
        # for pump in self.pumps:
        #     print('Q')
        #     print(np.mean(results[pump+'.Q']))
        #     print('dH')
        #     print(np.mean(results[pump+'.dH']))

        # print("Demands")
        # for d in self.demands:
        #     print(d)
        #     print("dH")
        #     print(np.mean(results[d+'.QTHOut.H']-results[d+'.QTHIn.H']))

        # print("STATS PIPES")
        # tot_pipe_heat_loss = 0.0
        # for p in self.pipes:
        #     print(p)
        #     print('dH')
        #     print(np.mean(results[p+'.dH']))
        #     print('T in')
        #     print(np.mean(results[p+'.QTHIn.T']))
        #     print('T out')
        #     print(np.mean(results[p+'.QTHOut.T']))
        #     t_out = results[p+'.QTHOut.T']
        #     t_in = results[p+'.QTHIn.T']
        #     q = results[p+'.Q']
        #     cp = self.parameters(0)[p+'.cp']
        #     rho = self.parameters(0)[p+'.rho']
        #     length = self.parameters(0)[p+'.length']
        #     U_1 = self.parameters(0)[p+'.U_1']
        #     U_2 = self.parameters(0)[p+'.U_2']
        #     T_g = self.parameters(0)[p+'.T_g']
        #     sign_dT = self.parameters(0)[p+'.sign_dT']
        #     dT = self.parameters(0)[p+'.T_supply'] - self.parameters(0)[p+'.T_return']
        #     heat_loss = length*(U_1-U_2)*(t_in + t_out)/2 -(length*(U_1-U_2)*T_g)+(length*U_2*(sign_dT*dT))
        #     temp_loss = heat_loss/(cp*rho*q)
        #     print("Avg heat loss in pipe")
        #     print(np.mean(heat_loss))
        #     tot_pipe_heat_loss += np.mean(heat_loss)
        #     print("Avg temperature loss in pipe")
        #     print(np.mean(temp_loss))
        #     print()

        # print("STATS SYSTEM WIDE")
        # # Heat
        # heat_sources = np.mean(results['source1.Heat']) + np.mean(results['source2.Heat'])
        # heat_demands = 0.0
        # for d in self.demands:
        #     k = d[6:]
        #     heat_demands += np.mean(self.get_timeseries('Heat_demand_'+k).values)
        # print("Avg tot heat from sources")
        # print(heat_sources)
        # print("Avg tot heat demand")
        # print(heat_demands)
        # print("Avg tot heat loss in pipes")
        # print(tot_pipe_heat_loss)
        # print("Differences in (total) conservation of heat")
        # print(heat_sources - (heat_demands+tot_pipe_heat_loss))
        # # Temperatures
        # t_supply = []
        # for p in self.pipe_profile_hot:
        #     t_supply_avg = (np.mean(results[p+'.QTHIn.T'])+np.mean(results[p+'.QTHOut.T']))/2
        #     t_supply.append((t_supply_avg))
        # print("Avg supply temperature system profile")
        # print(t_supply)
        # t_return = []
        # for p in self.pipe_profile_cold:
        #     t_return_avg = (np.mean(results[p+'.QTHIn.T'])+np.mean(results[p+'.QTHOut.T']))/2
        #     t_return.append((t_return_avg))
        # print("Avg return temperature system profile")
        # print(t_return)


        ### PLOTS ###

        sum_demands = np.full_like(self.times(), 0.0)
        for d in self.demands:
            k = d[6:]
            sum_demands +=self.get_timeseries('Heat_demand_'+k).values

        # Generate Heat Plot
        # This plot illustrates: 
        # * upper plot - heat of the sources and in/out from the storage;
        # * middle plot - stored heat in the buffer;
        # * lower plot - heat demand requested versus result of the optimization

        n_subplots = 3
        fig, axarr = plt.subplots(n_subplots, sharex=True, figsize=(8, 3 * n_subplots))
        axarr[0].set_title("Heat")

        # Upper subplot
        axarr[0].set_ylabel("Heat Sources")
        axarr[0].ticklabel_format(style="sci", axis="y", scilimits=(0, 0))
        axarr[0].plot(times, results["source1.Heat"], label="source1", linewidth=2, color="b", linestyle="--",)
        axarr[0].plot(
            times,
            results["source2.Heat"],
            label="source2",
            linewidth=2,
            color="r",
            linestyle="--",
        )
        axarr[0].plot(
            times,
            results["buffer1.Heat"],
            label="buffer",
            linewidth=2,
            color="g",
            linestyle="--",
        )

        # Middle Subplot
        axarr[1].set_ylabel("Stored Heat Buffer")
        axarr[1].plot(times, results['StoredHeat_buffer']/3600.0, label="Stored heat buffer", linewidth=2, color="g")

        # Lower Subplot
        axarr[2].set_ylabel("Demand")
        axarr[2].plot(times, self.get_timeseries('Heat_demand_7').values, label="demand7 req", linewidth=2, color="r")
        axarr[2].plot(times, results['demand7.Heat'], label="demand7 opt", linewidth=2, color="g", linestyle="--")
        axarr[2].plot(times, self.get_timeseries('Heat_demand_91').values, label="demand91&92 req", linewidth=2, color="r")
        axarr[2].plot(times, results['demand91.Heat'], label="demand91 opt", linewidth=2, color="g", linestyle="--")
        axarr[2].plot(times, results['demand92.Heat'], label="demand92 opt", linewidth=2, color="g", linestyle="--")

        # Shrink each axis and put a legend to the right of the axis
        for i in range(n_subplots):
            box = axarr[i].get_position()
            axarr[i].set_position([box.x0, box.y0, box.width * 0.8, box.height])
            axarr[i].legend(loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)

        # Output Plot
        plt.show()


        # # Plot 2: for a random pipe, info regarding the dH, Q relationship        
        # pipe = 'pipe25H'
        # diameter = self.parameters(0)[f'{pipe}.diameter']
        # area = math.pi * diameter**2 / 4
        # length = self.parameters(0)[f'{pipe}.length']
        # temperature = self.parameters(0)[f'{pipe}.temperature']
        # wall_roughness = 2E-3
        # n_lines=10
        # q_max = 0.01111*2            
        # v_max = q_max/area
        # q_points = np.linspace(0.0, q_max, 1000)
        # v_points = np.linspace(0.0, v_max, 1000)
        
        # # Linear coefficients#1 for Q-H relationship
        # gravitational_constant = 9.81
        # friction_factor = 0.04
        # c = length * friction_factor / (2 * gravitational_constant) / diameter

        # # Darcy Weisbach
        # dH_dw = np.full_like(q_points, 0.0)
        # for i in range(len(q_points)):
        #     dH_dw[i] = head_loss(v_points[i], diameter, length, wall_roughness, temperature)
        
        # # Plot2         
        # n_subplots = 3
        # fig, axarr = plt.subplots(n_subplots, sharex=False, figsize=(8, 3 * n_subplots))
        
        # #Plot2 Upper subplot: Q vs dH for DW and cQ^2
        # axarr[0].set_title("cQ2 vs Darcy Weisbach")
        # # axarr[0].set_xlabel("Discharge Q [m3/s]")
        # axarr[0].set_ylabel("dH [m]")      
        
        # axarr[0].plot(q_points, dH_dw, linewidth=2, color="r", linestyle="--", label="D-W")
        # axarr[0].plot(q_points, c*v_points**2, linewidth=2, color="b", label="cQ^2")    
        
        # #Plot2 Middle subplot: Q vs dH for  cv^2
        # # axarr[1].set_title("dH >= c*Q^2")
        # # axarr[1].set_xlabel("Discharge Q [m3/s]")
        # axarr[1].set_ylabel("dH [m]")
        # q_opt = results[pipe+'.Q']
        # dH_opt = -results[pipe+'.dH']
        # axarr[1].plot(q_points, c*v_points**2, linewidth=2, color="b", label="cQ^2")
        # axarr[1].plot(q_opt, dH_opt, linewidth=2, color="r", linestyle=":", label="opt") 

        # #Plot2 Lower subplot: Q vs dH for DW
        # # axarr[2].set_title("DW vs opt results")
        # axarr[2].set_xlabel("Discharge Q [m3/s]")
        # axarr[2].set_ylabel("dH [m]")
        # q_opt = results[pipe+'.Q']
        # dH_opt = -results[pipe+'.dH']
        # q_points_zoom = np.linspace(0.0, 0.01, 1000)
        # v_points_zoom = q_points_zoom/area
        #         # Darcy Weisbach
        # dH_dw_zoom = np.full_like(q_points_zoom, 0.0)
        # for i in range(len(q_points_zoom)):
        #     dH_dw_zoom[i] = head_loss(v_points_zoom[i], diameter, length, wall_roughness, temperature)
        # axarr[2].plot(q_points_zoom, dH_dw_zoom, linewidth=2, color="b", label="D-W")
        # axarr[2].plot(q_opt, dH_opt, linewidth=2, color="r", linestyle=":", label="opt") 

        # # Shrink each axis and put a legend to the right of the axis
        # for i in range(n_subplots):
        #     box = axarr[i].get_position()
        #     axarr[i].set_position([box.x0, box.y0, box.width * 0.8, box.height])
        #     axarr[i].legend(loc="center left", bbox_to_anchor=(1, 0.5), frameon=False)
        # plt.show()

        super().post()


# Run
start_time = time.time()

run_optimization_problem(Example)

# Output runtime
print("Execution time: " + time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time)))