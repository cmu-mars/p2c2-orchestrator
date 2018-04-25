#!/usr/bin/env python
from glob import glob
from setuptools import setup, find_packages

setup(
    name='orchestrator',
    version='0.0.1',
    description='Orchestrates the subsystems responsible for Phase II CP2',
    author='Chris Timperley',
    author_email='ctimperley@cmu.edu',
    url='https://github.com/cmu-mars/p2c2-orchestrator',
    license='mit',
    python_requires='>=3.5',
    install_requires=[
        'darjeeling>=0.0.18',
        'bugzoo>=2.0.15',
        # 'hulk',
        'requests',
        'flask'
    ],
    include_package_data=True,
    packages=find_packages('src'),
    package_dir={'': 'src'},
    py_modules=[splitext(basename(path))[0] for path in glob('src/*.py')],
    entry_points = {
        'console_scripts': [
            'orchestrator = orchestrator.server:main'
        ]
    }
)
