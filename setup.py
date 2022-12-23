#! /usr/bin/env python

from setuptools import setup, find_packages

with open('README.md', encoding='utf8') as f:
    long_description = f.read()

with open('requirements.txt', encoding='utf8') as f:
    install_requires = f.readlines()

setup(
    name='informa',
    version='0.1',
    description='API scraper and more based on rocketry',
    long_description_content_type='text/markdown',
    long_description=long_description,
    author='Matt Black',
    author_email='dev@mafro.net',
    url='https://github.com/mafrosis/informa',
    packages=find_packages(exclude=['test']),
    package_data={'': ['LICENSE']},
    package_dir={'': '.'},
    include_package_data=True,
    install_requires=install_requires,
    license='MIT License',
    entry_points={
        'console_scripts': [
            'informa=informa.main:main'
        ]
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'Natural Language :: English',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.9',
    ],
)
