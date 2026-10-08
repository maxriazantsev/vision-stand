import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'detector'

setup(
    name=package_name,
    # Not the real version, see package.xml.
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'models'), glob('models/*.onnx')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='max',
    maintainer_email='m4xh17@gmail.com',
    description='Vision Stand: camera + on-device object detection pipeline '
                'for a networked Raspberry Pi vision stand.',
    license='MIT',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'camera_node = detector.camera_node:main',
            'detector_node = detector.detector_node:main',
            'object_detector_node = detector.object_detector_node:main',
        ],
    },
)
