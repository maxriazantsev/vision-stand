from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    source_arg = DeclareLaunchArgument(
        'source', default_value='test_clip.mp4',
        description='camera_node source: video file path or camera device index')
    fps_arg = DeclareLaunchArgument(
        'fps', default_value='30.0', description='camera_node publish rate')
    compressed_arg = DeclareLaunchArgument(
        'compressed', default_value='false',
        description='also run image_transport republish to publish '
                    '/camera/image_raw/compressed')
    resolution_arg = DeclareLaunchArgument(
        'resolution', default_value='320',
        description='object_detector_node ONNX model input size: 320 or 640')
    conf_threshold_arg = DeclareLaunchArgument(
        'conf_threshold', default_value='0.5',
        description='object_detector_node minimum detection confidence')

    camera_node = Node(
        package='detector', executable='camera_node', name='camera_node',
        parameters=[{
            'source': ParameterValue(LaunchConfiguration('source'), value_type=str),
            'fps': LaunchConfiguration('fps'),
        }],
    )

    detector_node = Node(
        package='detector', executable='detector_node', name='detector_node',
    )

    object_detector_node = Node(
        package='detector', executable='object_detector_node',
        name='object_detector_node',
        parameters=[{
            'resolution': ParameterValue(
                LaunchConfiguration('resolution'), value_type=int),
            'conf_threshold': ParameterValue(
                LaunchConfiguration('conf_threshold'), value_type=float),
        }],
    )

    republish_node = Node(
        package='image_transport', executable='republish', name='image_republisher',
        arguments=['raw', 'compressed'],
        remappings=[
            ('in', '/camera/image_raw'),
            ('out/compressed', '/camera/image_raw/compressed'),
        ],
        condition=IfCondition(LaunchConfiguration('compressed')),
    )

    annotated_republish_node = Node(
        package='image_transport', executable='republish',
        name='annotated_republisher',
        arguments=['raw', 'compressed'],
        remappings=[
            ('in', '/detector/objects/annotated'),
            ('out/compressed', '/detector/objects/annotated/compressed'),
        ],
    )

    return LaunchDescription([
        source_arg, fps_arg, compressed_arg, resolution_arg, conf_threshold_arg,
        camera_node, detector_node, object_detector_node,
        republish_node, annotated_republish_node,
    ])
