#!/usr/bin/env python
from setuptools import setup, find_packages

install_requires = [
    'articlemetaapi',
    'certifi',
    'chardet',
    'dateutils',
    'greenlet',
    'idna',
    'langcodes',
    'legendarium',
    'lxml',
    'mysqlclient',
    'ply',
    'pymongo',
    'python-dateutil',
    'pytz',
    'requests',
    'reverse-geocode',
    'Sickle',
    'six',
    'SQLAlchemy',
    'thriftpy2',
    'urllib3',
    'xylose',
    'scielo_scholarly_data'
]


setup(
    name="scielo-usage-counter",
    version='0.7.5',
    description="The SciELO COUNTER Tools",
    author="SciELO",
    author_email="scielo-dev@googlegroups.com",
    license="BSD",
    url="https://github.com/scieloorg/access-counter",
    keywords='usage access, counter code of practice 5',
    maintainer_email='rafael.pezzuto@gmail.com',
    packages=find_packages(),
    install_requires=install_requires,
    entry_points="""
    [console_scripts]
    initialize_database=proc.initialize_database:main
    create_dictionaries=proc.create_dictionaries:main
    populate_journals=proc.populate_journals:main
    calculate_metrics=proc.calculate_metrics:main
    export_to_database=proc.export_to_database:main
    extract_pretables=proc.extract_pretables:main
    collect_opac_dictionary=proc.collect_opac_dictionary:main
    collect_preprint_dictionary=proc.collect_preprint_dictionary:main
    collect_articlemeta_dictionary=proc.collect_articlemeta_dictionary:main
    aggregate=proc.aggregate:main
    """
)
