from setuptools import setup
import os
import sys

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))
version = open('.version').read().lstrip('v')

LIBDIR = os.path.join(sys.prefix, 'lib')


def find_data_files(dest, path):
	result = []
	for root, dirs, files in os.walk(path):
		prefix = root
		if prefix.startswith(path):
			prefix = prefix[len(path):]
		prefix = prefix.lstrip('/')
		data_files = []
		for file in files:
			fullpath = os.path.join(root, file)
			if os.path.isfile(fullpath):
				data_files.append(fullpath)
		result.append((os.path.join(dest, prefix), data_files))
	return result


setup(
	name='connect-client',
	version=version,
	#include_package_data=True,
	#license='XXX',
	#description='XXX',
	#long_description=README,
	url='https://github.com/CI-Connect/connect-client',
	#author='XXX',
	#author_email='XXX',
	scripts=[
		'connect/bin/connect',
		'connect/bin/distribution',
	],
	data_files=find_data_files(LIBDIR, 'connect/lib'),
	classifiers=[
		'Intended Audience :: Users',
		#'License :: OSI Approved :: BSD License',
		'Operating System :: OS Independent',
		'Programming Language :: Python',
		'Programming Language :: Python :: 2.6',
		'Programming Language :: Python :: 2.7',
	],
	install_requires=[
		'paramiko',
		'pycrypto',
	],
)
