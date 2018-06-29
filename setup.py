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
        'darjeeling>=0.1.9',
        'bugzoo>=2.1.12',
        'boggart>=0.1.10',
        'rooibos>=0.3.0',
        'requests',
        'flask'
    ],
    # setup_requires=[
    #     'pytest-runner'
    # ],
    # tests_require=[
    #     'pytest'
    # ],
    packages=find_packages('src'),
    package_dir={'': 'src'},
    package_data={
        '': ['*.yml', 'data/*.json']
    },
    py_modules=[
        splitext(basename(path))[0] for path in glob('src/*.py')
    ],
    test_suite='tests',
    entry_points = {
        'console_scripts': [
            'orchestrator-instrument = orchestrator.instrument:instrument',
            'orchestrator-extract = orchestrator.donor:extract',
            'orchestrator-precompute = orchestrator.coverage:precompute'
        ]
    }
)
