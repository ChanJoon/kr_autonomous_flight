#ifndef ACTION_PLANNER_PLANNER_DETAILS_H_
#define ACTION_PLANNER_PLANNER_DETAILS_H_

#include <action_planner/data_conversions.h>
#include <action_planner/primitive_ros_utils.h>
#include <jps/jps_planner.h>
#include <kr_planning_msgs/PlanTwoPointAction.h>
#include <kr_planning_rviz_plugins/data_ros_utils.h>  //vec_to_cloud
#include <motion_primitives/graph_search.h>
#include <motion_primitives/utils.h>
#include <mpl_basis/trajectory.h>
#include <mpl_collision/map_util.h>
#include <mpl_planner/map_planner.h>
#include <plan_manage/planner_manager.h>
#include <traj_opt_ros/ros_bridge.h>
#include <traj_utils/planning_visualization.h>

#include <gcopter/planner.hpp>
// #include "altro/altro.hpp"
#include <kr_ilqr_optimizer/spline_trajectory_sampler.hpp>
// #include <kr_planning_msgs/Traj.h>

#include <chrono>
#include <memory>
#include <string>
#include <vector>
#include <action_planner/LocalPlannerConfig.h>

class PlannerType {
 public:
  explicit PlannerType(const ros::NodeHandle& nh, const std::string& frame_id)
      : nh_(nh), frame_id_(frame_id) {}
  virtual void setup() = 0;
  // always called by individual planner
  virtual kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map) {
    ROS_WARN("[Plannner Details]:plan spline not implemented for this planner");
    // std::logic_error("Function not yet implemented");
    return kr_planning_msgs::SplineTrajectory();
  }
  virtual kr_planning_msgs::TrajectoryDiscretized plan_discrete(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map) {
    ROS_WARN(
        "[Plannner Details]:plan discrete not implemented for this planner");
    // std::logic_error("Function not yet implemented");
    return kr_planning_msgs::TrajectoryDiscretized();
  }  // this does not have to be implemented

  // always called by composite planner
  virtual std::tuple<kr_planning_msgs::SplineTrajectory,
                     kr_planning_msgs::SplineTrajectory,
                     kr_planning_msgs::TrajectoryDiscretized>
  plan_composite( 
                 const action_planner::LocalPlannerConfig& config, 
                 const MPL::Waypoint3D& start,
                 const MPL::Waypoint3D& goal,
                 const kr_planning_msgs::VoxelMap& map,
                 const kr_planning_msgs::VoxelMap& map_no_inflation,
                 float* compute_time_front_end,
                 float* compute_time_back_end,
                 float* compute_time_poly,
                 int& success_status) {
    ROS_ERROR("[Plannner Details]:plan composite not implemented");
    // std::logic_error("Function not yet implemented");
    return std::make_tuple(kr_planning_msgs::SplineTrajectory(),
                           kr_planning_msgs::SplineTrajectory(),
                           kr_planning_msgs::TrajectoryDiscretized());
  }

  virtual void setConfig(const action_planner::LocalPlannerConfig& config) {
    ROS_ERROR("[Plannner Details]:setConfig not implemented");
  }

  virtual MPL::Waypoint3D evaluate(double t) = 0;
  void setGoal(const kr_planning_msgs::PlanTwoPointGoal& goal) {
    action_server_goal_ = goal;
  }
  double getTotalTrajTime() { return traj_total_time_; }
  void setSearchPath(const std::vector<Eigen::Vector3d>& search_path) {
    search_path_ = search_path;
  }
  std::vector<Eigen::Vector3d> SamplePath(double dt);

  bool has_collision(const kr_planning_msgs::VoxelMap& map);

  ros::NodeHandle nh_;
  std::string frame_id_;
  double traj_total_time_;
  // TODO(Laura) not sure if this is the best way to pass the search path
  std::vector<Eigen::Vector3d> search_path_;
  kr_planning_msgs::SplineTrajectory search_path_msg_;
  // If replanning, some planners requires the previous trajectory which is
  // contained in the action server goal
  kr_planning_msgs::PlanTwoPointGoal action_server_goal_;
  std::vector<Eigen::MatrixXd> hPolys;  // opt needs to have these
  Eigen::VectorXd allo_ts;
};

class CompositePlanner : public PlannerType {
 public:
  explicit CompositePlanner(const ros::NodeHandle& nh,
                            const std::string& frame_id)
      : PlannerType(nh, frame_id) {}
  void setup();
  // kr_planning_msgs::SplineTrajectory plan(
  //     const MPL::Waypoint3D& start,
  //     const MPL::Waypoint3D& goal,
  //     const kr_planning_msgs::VoxelMap& map);
  std::tuple<kr_planning_msgs::SplineTrajectory,
             kr_planning_msgs::SplineTrajectory,
             kr_planning_msgs::TrajectoryDiscretized>
  plan_composite(const action_planner::LocalPlannerConfig& config,
                 const MPL::Waypoint3D& start,
                 const MPL::Waypoint3D& goal,
                 const kr_planning_msgs::VoxelMap& map,
                 const kr_planning_msgs::VoxelMap& map_no_inflation,
                 float* compute_time_front_end,
                 float* compute_time_back_end,
                 float* compute_time_poly,
                 int& success_status);
  MPL::Waypoint3D evaluate(double t);

 private:
  PlannerType* search_planner_type_;
  PlannerType* opt_planner_type_;
  double path_sampling_dt_;
  ros::Publisher search_traj_pub_;
  opt_planner::PlannerManager::Ptr poly_generator_;
  std::shared_ptr<MPL::VoxelMapUtil> poly_gen_map_util_;
};

namespace OptPlanner {

class iLQR_Planner : public PlannerType {
 public:
  explicit iLQR_Planner(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}
  void setup(){};
  void setup(const double& moment_of_inertia_each, const double& dt_ilqr);
  kr_planning_msgs::TrajectoryDiscretized plan_discrete(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

  void setConfig(const action_planner::LocalPlannerConfig& config) {
    this->setup(config.moment_of_inertia_each, config.altro_dt);
    // ros::NodeHandle nh = ros::NodeHandle("~");
    // sampler_->mpc_solver->altro_ini_penalty = config.altro_ini_penalty;
    // sampler_->mpc_solver->altro_penalty_scale = config.altro_penalty_scale;
    // sampler_->mpc_solver->model_ptr->moment_of_inertia_(0,0) =
    //     config.moment_of_inertia_each;
    // sampler_->mpc_solver->model_ptr->moment_of_inertia_(1,1) =
    //     config.moment_of_inertia_each;
    // sampler_->mpc_solver->model_ptr->moment_of_inertia_(2,2) =
    //     config.moment_of_inertia_each;
  }

 private:
  SplineTrajSampler::Ptr sampler_;
  double ilqr_sampling_dt_ = 0.1;
  // currently initialized another planner just to get polytopes, maybe consider
  // moving only that function out
  std::vector<Eigen::VectorXd> opt_traj_;  // empty if no solution yet
};

class DoubleDescription : public PlannerType {
 public:
  explicit DoubleDescription(const ros::NodeHandle& nh,
                             const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  opt_planner::PlannerManager::Ptr planner_manager_;
  min_jerk::Trajectory opt_traj_;
  std::shared_ptr<MPL::VoxelMapUtil> mp_map_util_;
};

class GCOPTER : public PlannerType {
 public:
  explicit GCOPTER(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  gcopter::GcopterPlanner::Ptr planner_manager_;
  Trajectory<5> opt_traj_;
};

}  // namespace OptPlanner

namespace SearchPlanner {
class UniformInputSampling : public PlannerType {
 public:
  explicit UniformInputSampling(const ros::NodeHandle& nh,
                                const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  MPL::Trajectory3D mp_traj_;
  std::shared_ptr<MPL::VoxelMapPlanner> mp_planner_util_;
  std::shared_ptr<MPL::VoxelMapUtil> mp_map_util_;
  bool debug_;
  bool verbose_;
  double tol_pos_, goal_tol_vel_, goal_tol_acc_;
  bool use_3d_local_;
  ros::Publisher expanded_cloud_pub;
};

class Dispersion : public PlannerType {
 public:
  explicit Dispersion(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  double tol_pos_;
  std::string heuristic_;
  std::shared_ptr<motion_primitives::MotionPrimitiveGraph> graph_;
  std::vector<std::shared_ptr<motion_primitives::MotionPrimitive>>
      dispersion_traj_;
  ros::Publisher visited_pub_;
  motion_primitives::GraphSearch::Option options_;
};

class Geometric : public PlannerType {
 public:
  explicit Geometric(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  std::shared_ptr<JPS::JPSPlanner3D> jps_3d_util_;
  std::shared_ptr<JPS::VoxelMapUtil> jps_3d_map_util_;
  bool verbose_{true};
  kr_planning_msgs::SplineTrajectory spline_traj_;
  ros::Publisher path_pub_;
};

class PathThrough : public PlannerType {
 public:
  explicit PathThrough(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  void highLevelPlannerCB(const kr_planning_msgs::Path& path);
  bool verbose_{true};
  kr_planning_msgs::SplineTrajectory spline_traj_;
  ros::Publisher path_pub_;
  ros::Subscriber high_level_planner_sub_;
  kr_planning_msgs::Path path_ = kr_planning_msgs::Path();
};

// SST
class DynSampling : public PlannerType {
 public:
  explicit DynSampling(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  gcopter::GcopterPlanner::Ptr sstplanner_;
  bool verbose_{true};
  vec_Vecf<3> path_;
  kr_planning_msgs::SplineTrajectory spline_traj_;
  ros::Publisher path_pub_;
};

// RRT
class Sampling : public PlannerType {
 public:
  explicit Sampling(const ros::NodeHandle& nh, const std::string& frame_id)
      : PlannerType(nh, frame_id) {}

  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  gcopter::GcopterPlanner::Ptr rrtplanner_;
  bool verbose_{true};
  vec_Vecf<3> path_;
  kr_planning_msgs::SplineTrajectory spline_traj_;
  ros::Publisher path_pub_;
};

}  // namespace SearchPlanner

#endif  // ACTION_PLANNER_PLANNER_DETAILS_H_