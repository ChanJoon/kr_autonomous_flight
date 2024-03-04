#!/usr/bin/env python3
import rospy
# from kr_planning_msgs.msg import SplineTrajectory
from kr_planning_msgs.msg import PlanTwoPointAction, PlanTwoPointGoal, VoxelMap
from kr_mav_msgs.msg import PositionCommand, OutputData
from kr_tracker_msgs.msg import PolyTrackerGoal, PolyTrackerAction, LineTrackerAction, LineTrackerGoal
import numpy as np
# import matplotlib.pyplot as plt
import pandas as pd
from copy import deepcopy
from geometry_msgs.msg import Point
from visualization_msgs.msg import MarkerArray, Marker
from actionlib import SimpleActionClient
from std_srvs.srv import Empty, Trigger
from kr_tracker_msgs.srv import Transition
from nav_msgs.msg import Odometry
import random 
import actionlib
# from std_msgs.msg import Bool
from std_msgs.msg import Int32
from tqdm import tqdm
import pickle
import yaml
import csv

import pcl
#sudo apt install python3-pcl
import sensor_msgs.point_cloud2 as pc2
from sensor_msgs.msg import PointCloud2
from datetime import datetime
from param_env_msgs.srv import changeMap
import optuna
from dynamic_reconfigure.client import Client
import yaml

auto_tune = True
autotune_config_file = '/home/yifei/ws_mp_design/src/kr_autonomous_flight/autonomy_core/map_plan/action_planner/cfg/auto_tune_config.yaml'
poly_service_name = "/quadrotor/mav_services/poly_tracker"
line_service_name = "/quadrotor/trackers_manager/transition"
use_odom_bool = False
multi_front_end = False
line_tracker_flight_time = 6.0 # seconds

# filename = '/home/laura/autonomy_ws/src/kr_autonomous_flight/autonomy_core/map_plan/action_planner/scripts/map_balls_start_goal.csv'


def differentiate(p, segment_time):
    v = np.zeros(p.size - 1)
    for i in range(1, p.size):
        v[i-1] = (p[i] * i / segment_time)
    return v


def evaluate(msg, t, deriv_num):
    result = np.zeros(msg.dimensions)

    for dim in range(msg.dimensions):
        spline = msg.data[dim]
        dt = 0
        for poly in spline.segs:
            poly_coeffs = np.array(poly.coeffs)
            # print(poly_coeffs)
            for d in range(deriv_num):
                poly_coeffs = differentiate(poly_coeffs, poly.dt)
            result[dim] = poly_coeffs[0]

            if (t < dt + poly.dt or poly == spline.segs[-1]):
                for j in range(1, poly_coeffs.size):
                    result[dim] += poly_coeffs[j] * ((t - dt) / poly.dt) ** j
                break
            dt += poly.dt
    return result


class Evaluater:
    def __init__(self, autotune = False):
        # print("reading "+filename)
        # self.start_goals = pd.read_csv(filename)
        # self.path_pub = rospy.Publisher('/local_plan_server0/plan_local_trajectory/goal', PlanTwoPointActionGoal, queue_size=10, latch=True)
        # self.client = SimpleActionClient('/local_plan_server0/plan_local_trajectory', PlanTwoPointAction)
        self.client_list = []
        self.client_name_list = []
        self.client_name_front_list = []
        self.client_name_back_list = []
        self.dyn_reconf_client_list = []

        self.num_planners = 2
        self.num_trials = 20
        for i in range(self.num_planners): #  0, 1, 2, ... not gonna include the one with no suffix
            self.client_list.append(SimpleActionClient('/local_plan_server'+str(i)+'/plan_local_trajectory', PlanTwoPointAction))
            self.dyn_reconf_client_list.append(Client('/local_plan_server'+str(i)))
             # self.client2 = SimpleActionClient('/local_plan_server2/plan_local_trajectory', PlanTwoPointAction)
        # self.client3 = SimpleActionClient('/local_plan_server3/plan_local_trajectory', PlanTwoPointAction)

        self.start_and_goal_pub = rospy.Publisher('/start_and_goal', MarkerArray, queue_size=10, latch=True)


        self.mav_name    = rospy.get_param('/local_plan_server0/mav_name')
        self.map_type    = rospy.get_param('/local_plan_server0/map_name')
        self.mav_radius  = rospy.get_param('/local_plan_server0/mav_radius')


        self.wait_for_things = rospy.get_param('/local_plan_server0/trajectory_planner/use_tracker_client')
        self.map_read_mode = rospy.get_param('/' + self.map_type + "/map/mode")

        self.fix_start_end_location = (self.map_type == "read_grid_map" and self.map_read_mode == 1) # or structure_map 
        
        self.map_origin_x = rospy.get_param('/' + self.map_type + "/map/x_origin")
        self.map_origin_y = rospy.get_param('/' + self.map_type + "/map/y_origin")
        self.map_origin_z = rospy.get_param('/' + self.map_type + "/map/z_origin")

        self.map_range_x = rospy.get_param('/' + self.map_type + "/map/x_size")
        self.map_range_y = rospy.get_param('/' + self.map_type + "/map/y_size")
        self.map_range_z = rospy.get_param('/' + self.map_type + "/map/z_size")

        self.inflate_radius = rospy.get_param('/' + self.map_type + "/map/inflate_radius")
        
        self.set_state_pub      = rospy.Publisher( '/' + self.mav_name + '/set_state', PositionCommand, queue_size=1, latch=False)
        # self.client_tracker = actionlib.SimpleActionClient('/quadrotor/trackers_manager/poly_tracker/PolyTracker', PolyTrackerAction)
        self.client_line_tracker = actionlib.SimpleActionClient('/' + self.mav_name + '/trackers_manager/line_tracker_min_jerk/LineTracker', LineTrackerAction)
        rospy.Subscriber("global_cloud", PointCloud2, self.point_clouds_callback) # this needs to be ready early

        # self.change_map_pub     = rospy.Publisher('/' + self.map_name + '/change_map', Int32, queue_size=1)
        self.change_map_pub  = rospy.ServiceProxy('/' + self.map_type + '/change_map', changeMap)


        print("waiting for tracker trigger service")
        rospy.wait_for_service(poly_service_name)
        # self.poly_trigger = rospy.ServiceProxy(poly_service_name, Trigger)
        self.transition_tracker = rospy.ServiceProxy(line_service_name, Transition)
        rospy.Subscriber('/' + self.mav_name + "/odom", Odometry, self.odom_callback)
        self.effort_temp = 0.0 # these need to be defined before the callback
        self.effort_counter = 0
        rospy.Subscriber('/' + self.mav_name + "/quadrotor_simulator_so3/output_data", OutputData, self.sim_output_callback)
        rospy.logwarn("Change topic name of OutputData on real quadrotor")

        # rospy.Subscriber("/local_plan_server0/trajectory", SplineTrajectory, self.callback)
        self.success = np.zeros((self.num_trials, self.num_planners), dtype=bool)
        self.success_detail = -2*np.ones((self.num_trials, self.num_planners), dtype=int)
        self.traj_time = np.zeros((self.num_trials, self.num_planners))
        self.traj_cost = np.zeros((self.num_trials, self.num_planners))
        self.traj_jerk = np.zeros((self.num_trials, self.num_planners))
        self.poly_compute_time = np.zeros((self.num_trials, self.num_planners))
        self.compute_time_front = np.zeros((self.num_trials, self.num_planners))
        self.compute_time_back = np.zeros((self.num_trials, self.num_planners))
        self.tracking_error = np.zeros((self.num_trials, self.num_planners))
        self.effort = np.zeros((self.num_trials, self.num_planners)) #unit in rpm
        self.rho = 50  # TODO(Laura) pull from param or somewhere
        self.collision_front = np.zeros((self.num_trials, self.num_planners), dtype=bool)
        self.collision_cnt = np.zeros((self.num_trials, self.num_planners), dtype=bool)
        self.dist_to_goal = np.zeros((self.num_trials, self.num_planners))

        self.kdtree = None
        self.pcl_data = None
        
        if autotune:
            with open(autotune_config_file, 'r') as file:
                self.param_config = yaml.safe_load(file)
            # maxmize success rate
            # currently only tune the first planner
            self.num_planners = 1
            self.publisher()
            self.study = optuna.create_study(study_name='ECI', direction='maximize')
            self.study.optimize(self.all_objectives, n_trials=100)
            print("Best params: ", self.study.best_params)
            print("Best value: ", self.study.best_value)
            df = self.study.trials_dataframe(attrs=("number", "value", "params", "state"))
            now_save = datetime.now()
            study_file_time = now_save.strftime("%m-%d_%H-%M-%S")
            with open('ECI_study'+study_file_time+'.csv', 'w') as f:
                f.write(df.to_csv())
            with open('ECI_study'+study_file_time+'.pkl', 'wb') as f:
                pickle.dump(self.study, f)
        else:
            self.publisher(self.num_trials)
            
    def suggest_params(self, trial, planner_i): # this should return config based on the available parameters of the planner
        config = {}
        # for param in self.param_config[self.client_name_front_list[planner_i]]:
        #     if param['type'] == 'float':
        #         config[param['name']] = trial.suggest_float(param['name'], param['bounds'][0], param['bounds'][1])
        #     elif param['type'] == 'int':
        #         config[param['name']] = trial.suggest_int(param['name'], param['bounds'][0], param['bounds'][1])
        for param in self.param_config[self.client_name_back_list[planner_i]]:
            if param['type'] == 'float':
                config[param['name']] = trial.suggest_float(param['name'], param['bounds'][0], param['bounds'][1])
            elif param['type'] == 'int':
                config[param['name']] = trial.suggest_int(param['name'], param['bounds'][0], param['bounds'][1])
        # for param in self.param_config['common']:
        #     if param['type'] == 'float':
        #         config[param['name']] = trial.suggest_float(param['name'], param['bounds'][0], param['bounds'][1])
        #     elif param['type'] == 'int':
        #         config[param['name']] = trial.suggest_int(param['name'], param['bounds'][0], param['bounds'][1])
        print ('Suggesting Params:' ,config)
        return config
    def all_objectives(self, trial, i = 0):
        # Suggest parameters based on YAML configuration
        # for i in range(self.num_planners):
        config = self.suggest_params(trial, i)
        # Update ROS parameters dynamically
        self.dyn_reconf_client_list[i].update_configuration(config)
        success_all_methods = self.publisher(20)
        return success_all_methods[0]



    def sample_in_map(self, tol):

        curr_sample_idx = 0
        start_end_feasible = True
        max_iter = 200
        while curr_sample_idx < max_iter:
        
            rand_start_x = random.uniform(self.map_origin_x + 1 ,  self.map_range_x + self.map_origin_x - 1)
            rand_start_y = random.uniform(self.map_origin_y + 1 ,  self.map_range_y + self.map_origin_y - 1)
            rand_start_z = random.uniform(self.map_origin_z + 0.5, self.map_range_z + self.map_origin_z - 0.5)

            rand_goal_x = random.uniform(self.map_origin_x + 1 ,  self.map_range_x + self.map_origin_x - 1)
            rand_goal_y = random.uniform(self.map_origin_y + 1,   self.map_range_y + self.map_origin_y - 1)
            rand_goal_z = random.uniform(self.map_origin_z + 0.5, self.map_range_z + self.map_origin_z - 0.5)

            start = np.array([rand_start_x, rand_start_y, rand_start_z])
            goal = np.array([rand_goal_x, rand_goal_y, rand_goal_z])
            

            curr_sample_idx += 1
            dis = np.linalg.norm(start - goal)
            
            #check dist is far and collision free
            if dis > 0.7 * self.map_range_x and not self.evaluate_collision([Point(x=start[0], y=start[1], z=start[2]), Point(x=goal[0], y=goal[1], z=goal[2])], tol):
                break
            
        if curr_sample_idx >= max_iter:
            rospy.logerr("Failed to sample a start and goal pair far enough apart && collision free")
            start_end_feasible = False
        return start, goal, start_end_feasible


    def odom_callback(self, msg):
        self.odom_data = msg.pose.pose.position

    def sim_output_callback(self, msg): #ToDo: This also has odom inside, consider combine with previous callback 
        self.effort_temp += np.mean(msg.motor_rpm) #rpm
        self.effort_counter += 1

    def point_clouds_callback(self, msg):
        tqdm.write("point cloud CALLBACK received")
        points_list = []

        for data in pc2.read_points(msg, skip_nans=True):
            points_list.append([data[0], data[1], data[2]])

        self.pcl_data = pcl.PointCloud(np.array(points_list, dtype=np.float32))


        self.kdtree = self.pcl_data.make_kdtree_flann()

        return
    

    def evaluate_collision(self, pts, tol = -1.0):
        if tol < 0:
            tol = self.mav_radius
        min_sq_dist = 1000000
        min_idx = 0
        if len(pts) > 200:
            pts = pts[::10]
        # tqdm.write("odom length = " + str( len(pts)))
        if self.kdtree is None:
            rospy.sleep(0.1)
            rospy.logerr("No KD Tree, skipping collision check")
            return True # assume collision
        for pt_idx in range(len(pts)):
            pt = pts[pt_idx]
            sp = pcl.PointCloud()
            sps = np.zeros((1, 3), dtype=np.float32)
            sps[0][0] = pt.x
            sps[0][1] = pt.y
            sps[0][2] = pt.z
            sp.from_array(sps)
            [ind, sqdist] = self.kdtree.nearest_k_search_for_cloud(sp, 1) #which pointcloud pt has min dist
            if sqdist[0][0] < min_sq_dist:

                min_sq_dist = sqdist[0][0]
                min_idx = pt_idx

        # tqdm.write("min dist = " + str(np.sqrt(min_sq_dist)) + "@ traj percentage = " + str(min_idx/len(pts)))
        if np.sqrt(min_sq_dist) < tol:

            if tol == self.mav_radius:
                rospy.logwarn("Collision Detected")
                print("np.sqrt(min_sq_dist) is ", np.sqrt(min_sq_dist))
            return True
        else:
            # print("min dist is ", np.sqrt(min_sq_dist))
            return False            

    def computeJerk(self, traj):
        # creae empty array for time
        t_vec = np.array([])
        # create empty array for jerk norm sq
        jerk_sq = np.array([])
        # jerk = 0
        dt = .01
        for t in np.arange(0, traj.data[0].t_total, dt):
            t_vec = np.append(t_vec, t)
            jerk_sq = np.append(jerk_sq, (np.linalg.norm(evaluate(traj, t, 3)))**2 )
        return np.sqrt(np.trapz(jerk_sq, t_vec))/traj.data[0].t_total
        

    def computeCost(self, traj, rho):
        time = traj.data[0].t_total
        cost = rho*time + self.computeJerk(traj)
        return cost
    def send_start_goal_viz(self, msg):
        start_and_goal = MarkerArray()
        start = Marker()
        start.header.frame_id = "map"
        start.header.stamp = rospy.Time.now()
        start.pose.position = msg.p_init.position
        start.pose.orientation.w = 1
        start.color.g = 1
        start.color.a = 1
        start.type = 2
        start.scale.x = start.scale.y = start.scale.z = 1
        goal = deepcopy(start)
        goal.pose.position = msg.p_final.position
        goal.id = 1
        goal.color.r = 1
        goal.color.g = 0
        start_and_goal.markers.append(start)
        start_and_goal.markers.append(goal)
        # self.path_pub.publish(msg)
        self.start_and_goal_pub.publish(start_and_goal) # viz
    def publisher(self, num_trials_local = 1):
        search_planner_text = rospy.get_param('/local_plan_server0/trajectory_planner/search_planner_text')
        opt_planner_text = rospy.get_param('/local_plan_server0/trajectory_planner/opt_planner_text')
        print("Running ", self.num_planners, "planner combinations for", self.num_trials, "trials", "on map", self.map_type)
        for i in range(self.num_planners):
            print("waiting for action server ", i)
            self.client_list[i].wait_for_server()
            planner_name_front = search_planner_text[rospy.get_param('/local_plan_server'+str(i)+'/trajectory_planner/search_planner_type')]
            planner_name_back  = opt_planner_text[rospy.get_param('/local_plan_server'+str(i)+'/trajectory_planner/opt_planner_type')]
            self.client_name_front_list.append(planner_name_front)
            self.client_name_back_list.append(planner_name_back)
            self.client_name_list.append(planner_name_front + '+'+ planner_name_back)
       
            
        print("All action server connected, number of planners = ", self.num_planners)
        now = datetime.now()
        file_name_save_time = now.strftime("%m-%d_%H-%M-%S")

        param_names = rospy.get_param_names()
        params = {}
        for name in param_names:
            params[name] = rospy.get_param(name)

        with open('ECI_params_'+file_name_save_time+'.yaml', 'w') as f:
            yaml.dump(params, f)

        try:
            with open('ECI_single_line_'+file_name_save_time+'.csv', 'w') as csvfile:
                csv_writer = csv.writer(csvfile)
                csv_writer.writerow(['map_type', 'map_seed','map_filename', 'density_index', 'clutter_index', 'structure_index',
                                     'start_end_feasible', 'planner_frontend', 'planner_backend', 
                                     'success', 'success_detail', 'traj_time(s)', 'traj_length(m)', 'traj_jerk', 'traj_effort(rpm)',
                                     'compute_time_poly(ms)', 'compute_time_frontend(ms)', 'compute_time_backend(ms)', 
                                     'tracking_error(m) avg', 'collision_frontend', 'collision_status','dist_to_goal(m)'])

                for i in tqdm(range(num_trials_local)):
                    if rospy.is_shutdown():
                        break
                    ######## CHANGE MAP ######
                    seed_val = i
                    random.seed(seed_val)


                    map_response = self.change_map_pub(seed = seed_val) # using seed is only active when using structure map
                    tqdm.write("Map Changed!!!!!")
                    rospy.sleep(5) # maze map is still reading files sequentially
                        # When change_map returns, the map is changed, but becuase delay, wait a little longer
                    # print(map_response)
                    start_end_feasible = True
                    ####### DEFINE START #####
                    # define start location not actually sending pos_msg since we using a tracker to get there
                    if not use_odom_bool: # hopefully this is always the case, we can specify the start
                        pos_msg = PositionCommand() # change position in simulator
                        pos_msg.header.frame_id = "map"
                        pos_msg.header.stamp = rospy.Time.now()

                        if self.fix_start_end_location:
                            start = np.array([-9.0, -4, 1.0])
                            end = np.array([9.0, 4, 1.0])
                        else:
                            start, end, start_end_feasible = self.sample_in_map(tol = self.mav_radius + self.inflate_radius + 0.1) #0.1 for fillMap_inflation
                        if not start_end_feasible:
                            continue
                        pos_msg.position.x = start[0]
                        pos_msg.position.y = start[1]
                        pos_msg.position.z = start[2]

                        pos_msg.velocity.x = 0
                        pos_msg.velocity.y = 0
                        pos_msg.velocity.z = 0
                        pos_msg.yaw = random.uniform(-np.pi,np.pi)

 
                        ##### GO TO START #####
                    if not use_odom_bool and self.wait_for_things:  #this needs to be done for every client 
                        traj_act_msg = LineTrackerGoal()
                        traj_act_msg.x = pos_msg.position.x
                        traj_act_msg.y = pos_msg.position.y
                        traj_act_msg.z = pos_msg.position.z
                        traj_act_msg.yaw = pos_msg.yaw
                        traj_act_msg.v_des = 0.0
                        traj_act_msg.a_des = 0.0
                        traj_act_msg.relative = False
                        traj_act_msg.t_start = rospy.Time.now()
                        traj_act_msg.duration = rospy.Duration(line_tracker_flight_time)
                        self.client_line_tracker.send_goal(traj_act_msg)# first change tracker goal msg
                        #wait while tracker's goal is not received
                        while True:
                            rospy.sleep(0.1)
                            state = self.client_line_tracker.get_state()
                            if state == 1:
                                tqdm.write("Line Tracker Goal Received")
                                break
                            rospy.loginfo_throttle(0.5,f"Waiting for line tracker goal. Current State: {state}")
                        # state = self.client_line_tracker.get_state() # make sure it received it
                        # print(f"After sent goal: Action State: {state}")
                        response = self.transition_tracker('kr_trackers/LineTrackerMinJerk')
                        # self.set_state_pub.publish(pos_msg) #then change state so no error remain

                        tqdm.write(response.message)

                        self.client_line_tracker.wait_for_result(rospy.Duration.from_sec(line_tracker_flight_time + 3.0)) #flying
                        response = self.client_line_tracker.get_result()
                        if response is not None:
                            tqdm.write("Line Tracker Finished")
                        else:
                            tqdm.write("Line Tracker Failed!!!!!!!!!!!!!")


                        ##### SET GOAL #####
                    msg = PlanTwoPointGoal()
                    if use_odom_bool:
                        msg.p_init.position = self.odom_data # if starting from current position
                        msg.p_final.position.z = self.odom_data.z # this is usually hardware, so z is more sensitive
                    else:
                        msg.p_init.position = pos_msg.position # if starting from random position
                        msg.p_final.position.z = end[2] # this is not hardware, so set it to whatever
                    # set goal to be random
                    msg.p_final.position.x = end[0]
                    msg.p_final.position.y = end[1]
                    msg.check_vel = False

                    self.send_start_goal_viz(msg)
                    for client_idx in range(self.num_planners):
                        client = self.client_list[client_idx]
                        client.send_goal(msg) #motion
                    result_list = []
                    valid_result = True
                    for client_idx in range(self.num_planners):
                        client = self.client_list[client_idx]

                        self.effort_temp = 0.0 # 
                        self.effort_counter = 1 # to avoid divide by zero

                        # Waits for the server to finish performing the action.
                        if self.wait_for_things:
                            client.wait_for_result(rospy.Duration.from_sec(20.0)) 
                        else:
                            client.wait_for_result(rospy.Duration.from_sec(20.0)) 
                
                        # stop accumulating the effort
                        self.effort[i,client_idx] = self.effort_temp / self.effort_counter
                        result = client.get_result()
                        if not result:
                            tqdm.write("Server Failure: trial " + str(i) + " planner: " + str(client_idx))
                            valid_result = False
                            break
                        result_list.append(result)
                    if valid_result:
                        for client_idx in range(self.num_planners):
                            result = result_list[client_idx]
                            #TODO(Laura) check if the path is collision free and feasible
                            if result:
                                self.success[i,client_idx] = result.success
                                tqdm.write("Solve Status: trial "+ str(seed_val) + " planner: " + str(client_idx) + " status: "+ str(result.policy_status))
                                self.success_detail[i,client_idx] = result.policy_status
                                # print(result.odom_pts) #@Yuwei: this should work, try this out! 
                                # Odom is also returned in result.odom_pts
                                    # rospy.loginfo(result.odom_pts)

                                self.poly_compute_time[i,client_idx] = result.computation_time
                                self.compute_time_front[i,client_idx] = result.compute_time_front_end
                                self.compute_time_back[i,client_idx] = result.compute_time_back_end
                                self.tracking_error[i,client_idx] = result.tracking_error
                                if result.success:
                                
                                    self.traj_time[i,client_idx] = result.traj.data[0].t_total
                                    # self.traj_cost[i,client_idx] = self.computeCost(result.traj, self.rho)
                                    self.traj_jerk[i,client_idx] = self.computeJerk(result.traj)
                                    if ~self.wait_for_things: # if no tracking then check collision of the planned traj
                                        result.odom_pts.clear()
                                        for t in np.arange(0, result.traj.data[0].t_total, 0.02):
                                            pos_t = evaluate(result.traj, t, 0)
                                            pos_t_pt = Point()
                                            pos_t_pt.x = pos_t[0]
                                            pos_t_pt.y = pos_t[1]
                                            pos_t_pt.z = pos_t[2]

                                            result.odom_pts.append(pos_t_pt)
                                        self.collision_cnt[i,client_idx] = self.evaluate_collision(result.odom_pts)
                                        result.odom_pts.clear()
                                        for t in np.arange(0, result.search_traj.data[0].t_total, 0.02):
                                            pos_t = evaluate(result.search_traj, t, 0)
                                            pos_t_pt = Point()
                                            pos_t_pt.x = pos_t[0]
                                            pos_t_pt.y = pos_t[1]
                                            pos_t_pt.z = pos_t[2]

                                            result.odom_pts.append(pos_t_pt)
                                        self.collision_front[i,client_idx] = self.evaluate_collision(result.odom_pts)
                                        
                                    else:
                                        self.collision_cnt[i,client_idx] = self.evaluate_collision(result.odom_pts)
                                    

                                    traj_end_point = np.zeros(3, dtype=np.float32)
                                    traj_end_point[0] = result.odom_pts[-1].x
                                    traj_end_point[1] = result.odom_pts[-1].y
                                    traj_end_point[2] = result.odom_pts[-1].z


                                    self.dist_to_goal[i, client_idx] = np.linalg.norm(end - traj_end_point)
    
                            else:
                                tqdm.write("Server Failure: trial " + str(i) + " planner: " + self.client_name_front_list[client_idx]+ self.client_name_back_list[client_idx])
                                self.success_detail[i,client_idx] = -1
                            
                    #dont have traj length, so just put 0
                            csv_writer.writerow([self.map_type, seed_val, map_response.file_name.data, map_response.density_index, map_response.clutter_index, map_response.structure_index,
                                                start_end_feasible, self.client_name_front_list[client_idx], self.client_name_back_list[client_idx],
                                                self.success[i,client_idx], self.success_detail[i,client_idx], self.traj_time[i,client_idx], 0.0, self.traj_jerk[i,client_idx], self.effort[i,client_idx],
                                                self.poly_compute_time[i,client_idx], self.compute_time_front[i,client_idx], self.compute_time_back[i,client_idx],
                                                self.tracking_error[i,client_idx], self.collision_front[i,client_idx], self.collision_cnt[i,client_idx], self.dist_to_goal[i, client_idx]])
                    # input("Press Enter to continue...")
        except KeyboardInterrupt:
            tqdm.write("Keyboard Interrupt!")


        #save results
        param_names = rospy.get_param_names()
        params = {}
        for name in param_names:
            params[name] = rospy.get_param(name)
        data_all = {}

        data_all['success'] = self.success
        data_all['success_detail'] = self.success_detail
        data_all['traj_time'] = self.traj_time
        data_all['traj_cost'] = self.traj_cost
        data_all['traj_jerk'] = self.traj_jerk
        data_all['poly_compute_time'] = self.poly_compute_time
        data_all['compute_time_front'] = self.compute_time_front
        data_all['compute_time_back'] = self.compute_time_back
        data_all['tracking_error'] = self.tracking_error
        data_all['effort'] = self.effort
        data_all['collision_front'] = self.collision_front
        data_all['collision_cnt'] = self.collision_cnt
        data_all['dist_to_goal'] = self.dist_to_goal

        print(self.success)
        print("Legend: -2: not run, -1: server failure, 0: front failure, 1: front success, 2: poly success, 3: back success")
        print(self.success_detail)
        print("Traj Time", self.traj_time)
        print("Traj Cost",self.traj_cost)
        print("Jerk", self.traj_jerk)
        print("Compute Time Front", self.compute_time_front)
        print("Compute Time Poly", self.poly_compute_time)
        print("Compute Time Back", self.compute_time_back)
        print("Tracking Error", self.tracking_error)
        print("Effort", self.effort)
        print("Is Collide Front", self.collision_front)
        print("Is Collide", self.collision_cnt)
        print("Distance To Goal", self.dist_to_goal)



        #save pickle with all the data, use date time as name
        # with open('ECI_eval_data_'+file_name_save_time+'.pkl', 'wb') as f:
        #     pickle.dump([self.success, self.success_detail, self.traj_time, self.traj_cost, self.traj_jerk, self.traj_compute_time, self.compute_time_front, self.compute_time_back, self.tracking_error, self.effort], f)
        with open('ECI_Result_' + file_name_save_time + '.pkl', 'wb') as f:
            pickle.dump(data_all, f)# result and config

        #create variables to store the average values
        success_front_rate = np.sum(self.success_detail >= 1, axis = 0)/self.success.shape[0]
        success_rate_avg = np.sum(self.success,axis = 0)/self.success.shape[0]
        traj_time_avg = np.sum(self.traj_time,axis = 0) / np.sum(self.success, axis = 0)
        # traj_cost_avg = np.sum(self.traj_cost[self.success]) / np.sum(self.success)
        traj_jerk_avg = np.sum(self.traj_jerk, axis = 0) / np.sum(self.success, axis = 0)
        poly_compute_time_avg = np.sum(self.poly_compute_time, axis = 0) / np.sum(self.success, axis = 0)
        compute_time_front_avg = np.sum(self.compute_time_front, axis = 0) / np.sum(self.success, axis = 0)
        compute_time_back_avg = np.sum(self.compute_time_back, axis = 0) / np.sum(self.success, axis = 0)
        tracking_error_avg = np.sum(self.tracking_error, axis = 0) / np.sum(self.success, axis = 0)
        effort_avg = np.sum(self.effort, axis = 0) / np.sum(self.success, axis = 0)
        collision_rate_avg = np.sum(self.collision_front, axis = 0) / np.sum(self.success, axis = 0)
        collision_rate_avg = np.sum(self.collision_cnt, axis = 0) / np.sum(self.success, axis = 0)
        dist_to_goal_avg = np.sum(self.dist_to_goal, axis = 0) / np.sum(self.success, axis = 0)
        # rewrite the above section with defined avg variables
        print("frontend success rate: " + str(success_front_rate)+ " out of " + str(self.success.shape[0]))
        print("success rate: " + str(success_rate_avg)+ " out of " + str(self.success.shape[0]))
        print("avg traj time(s): " + str(traj_time_avg))
        # print("avg traj cost(time + jerk): " + str(traj_cost_avg))
        print("avg traj jerk: " + str(traj_jerk_avg))
        print("avg compute time front(ms): " + str(compute_time_front_avg))
        print("avg compute time poly(ms): " + str(poly_compute_time_avg))
        print("avg compute time back(ms): " + str(compute_time_back_avg))
        print("avg tracking error(m): " + str(tracking_error_avg))
        print("avg effort(rpm): " + str(effort_avg))# this is bugg!! need to consider success
        print("avg dist to goal: " + str(dist_to_goal_avg))
        print("collision rate: " + str(collision_rate_avg))


        
        # save the avg values to a csv file by appending to the end of the file
        csv_name = 'ECI_Summary_'+file_name_save_time+'.csv'

        with open(csv_name, 'w') as f: #result summary
            writer = csv.writer(f)
            writer.writerow(['Map:'+self.map_type+ ' Run:' + str(self.num_trials),'success rate', 'frontend success','traj time', 'traj jerk', 'compute time(ms)', 'compute time front(ms)', 'compute time back(ms)', 'tracking error(m)', 'effort(rpm)', 'collision rate'])
            for i in range(self.num_planners):
                writer.writerow([self.client_name_list[i], success_rate_avg[i], success_front_rate[i], traj_time_avg[i], traj_jerk_avg[i], poly_compute_time_avg[i], compute_time_front_avg[i], compute_time_back_avg[i], tracking_error_avg[i], effort_avg[i], collision_rate_avg[i]])
        return success_rate_avg

def subscriber(autotune = False):
    rospy.init_node('evaluate_traj')
    Evaluater(autotune)

    # spin() simply keeps python from exiting until this node is stopped
    # rospy.spin()


if __name__ == '__main__':
    try:
        subscriber(auto_tune)
    except rospy.ROSInterruptException:
        pass
