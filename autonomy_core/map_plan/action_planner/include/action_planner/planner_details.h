#ifndef ACTION_PLANNER_PLANNER_DETAILS_H_
#define ACTION_PLANNER_PLANNER_DETAILS_H_

#include <action_planner/data_conversions.h>
#include <action_planner/primitive_ros_utils.h>
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

class PlannerType {
 public:
  explicit PlannerType(const ros::NodeHandle& nh, const std::string& frame_id)
      : nh_(nh), frame_id_(frame_id) {}
  virtual void setup() = 0;
  virtual kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map) = 0;

  virtual MPL::Waypoint3D evaluate(double t) = 0;
  void setGoal(const kr_planning_msgs::PlanTwoPointGoal& goal) {
    action_server_goal_ = goal;
  }
  double getTotalTrajTime() { return traj_total_time_; }
  void setSearchPath(const std::vector<Eigen::Vector3d>& search_path) {
    search_path_ = search_path;
  }
  std::vector<Eigen::Vector3d> SamplePath();

  ros::NodeHandle nh_;
  std::string frame_id_;
  double traj_total_time_;
  //TODO(Laura) pass as param
  double path_sampling_dt_ = 0.15;
  // TODO(Laura) not sure if this is the best way to pass the search path
  std::vector<Eigen::Vector3d> search_path_;
  // If replanning, some planners requires the previous trajectory which is
  // contained in the action server goal
  kr_planning_msgs::PlanTwoPointGoal action_server_goal_;
};

class CompositePlanner : public PlannerType {
 public:
  explicit CompositePlanner(const ros::NodeHandle& nh,
                            const std::string& frame_id)
      : PlannerType(nh, frame_id) {}
  void setup();
  kr_planning_msgs::SplineTrajectory plan(
      const MPL::Waypoint3D& start,
      const MPL::Waypoint3D& goal,
      const kr_planning_msgs::VoxelMap& map);
  MPL::Waypoint3D evaluate(double t);

 private:
  PlannerType* search_planner_type_;
  PlannerType* opt_planner_type_;
  ros::Publisher search_traj_pub_;
};

namespace OptPlanner {
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
}  // namespace OptPlanner

namespace SearchPlanner {
class UniformSampling : public PlannerType {
 public:
  explicit UniformSampling(const ros::NodeHandle& nh,
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
  double tol_pos_, tol_vel_;
  std::string heuristic_;
  std::shared_ptr<motion_primitives::MotionPrimitiveGraph> graph_;
  std::vector<std::shared_ptr<motion_primitives::MotionPrimitive>>
      dispersion_traj_;
  ros::Publisher visited_pub_;
};
}  // namespace SearchPlanner

#endif  // ACTION_PLANNER_PLANNER_DETAILS_H_