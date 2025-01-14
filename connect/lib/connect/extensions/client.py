#!/usr/bin/env python
#
# XXX TODO
#
# 1. use a uuid file to verify that local and remote project dirs match
#    * now stores client-side in .connect/juid
#    * how to ensure that we're dealing with the right remote?
#      - name remote dir ~ juid
#      - but how to map canonical repo name to juid? (this only
#        matters for clone operations - i.e. pull when no local
#        juid present)
#      - store canonical name in .connect also?
#    OR
#      - name remote dir ~ canonical
#      - compare juid files in protocol
#      - can't have two remote with same name
#
# 2. keyfile should be part of profile
#    - path to the profile's key stored anywhere, path in the profile def'n


import os
import sys
import getopt
import pwd
import socket
import getpass
import tempfile
import random
import time
import select
import uuid
import new
import urllib
import stat
import signal
import errno
import stat
import json
import subprocess
import hashlib
import ConfigParser
import textwrap
_version = '@@version@@'

defaults = '''
[server]
staging = %(home)s
'''

def help():
	m = main()
	return m._help()


DEFAULT_CLIENT_SERVER = 'connect-client.osgconnect.net'

class GeneralException(Exception):
	def __iadd__(self, other):
		if isinstance(other, (str, unicode)):
			self.args = self.args + (other,)
		else:
			self.args = self.args + tuple(args)

	def bubble(self, *args):
		self.args = self.args + tuple(args)
		raise self


class SSHError(GeneralException): pass
class UsageError(GeneralException): pass
class NotPresentError(GeneralException): pass
class NoRepoError(GeneralException): pass
class InvalidProfile(GeneralException): pass

class codes(object):
	OK = 200
	MULTILINE = 201
	YES = 202
	WAT = 401
	NO = 402
	NOTPRESENT = 403
	FAILED = 404
	

def units(n):
	_ = 'bkmgtpezy'
	while n > 10240 and _:
		n /= 1024
		_ = _[1:]
	return '%.4g%s' % (n, _[0])


def cleanfn(fn):
	fn = os.path.normpath(fn)
	while True:
		if fn.startswith('/'):
			fn = fn.lstrip('/')
		elif fn.startswith('./'):
			fn = fn[2:]
		elif fn.startswith('../'):
			fn = fn[3:]
		else:
			break

	return fn


def quote(s, chr='"'):
	return chr + s + chr


def ttysize():
	'''return (rows, columns) of controlling terminal'''
	import termios, fcntl, struct, os

	def ioctl(fd):
		r = fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234')
		yx = struct.unpack('hh', r)
		return yx

	try:
		fd = os.open(os.ctermid(), os.O_RDONLY)
		y, x = ioctl(fd)
		os.close(fd)

	except:
		y, x = 24, 80

	return map(int, (os.environ.get('LINES', y), os.environ.get('COLS', x)))


def mergeconfig(into, *args, **kwargs):
	if 'overwrite' in kwargs:
		overwrite = kwargs['overwrite']
	else:
		overwrite = True

	if 'sections' in kwargs:
		sections = kwargs['sections']
	else:
		sections = []

	for cfg in args:
		for section in cfg.sections():
			if sections and section not in sections:
				continue
			if not into.has_section(section):
				into.add_section(section)
			for option, value in cfg.items(section):
				if overwrite or (not into.has_option(section, option)):
					into.set(section, option, value)
	return into


class ClientSession(object):
	remotecmd = ['connect', 'client', '--server-mode']

	def __init__(self, hostname, port=22, user=None, keyfile=None, password=None, debug=None, repo=None):
		self.hostname = hostname
		self.port = port
		self.ssh = None
		self.version = 0
		self.transport = None
		self.channels = []
		self.user = user
		self.keyfile = keyfile
		self.password = password
		self.isdebug = False
		self.repo = repo

		if debug:
			self.debug = debug
			self.isdebug = True
		else:
			self.debug = lambda *args: True

		err = self.connect()
		if err:
			raise SSHError, 'Client authentication failed'
		if not self.ssh:
			raise SSHError, 'Client authentication failed'

		self.transport = self.ssh.get_transport()


	def connect(self):
		if self.user is None:
			self.user = getpass.getuser()
		if self.keyfile is None and self.password is None:
			self.password = getpass.getpass('Password for %s@%s: ' % (self.user, self.hostname))

		self.ssh = paramiko.SSHClient()
		self.ssh.load_system_host_keys()
		self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
		try:
			self.ssh.connect(self.hostname, username=self.user, port=self.port,
			                 password=self.password, key_filename=self.keyfile)
			return None
		except paramiko.AuthenticationException, e:
			return e
		except socket.gaierror, e:
			raise GeneralException, 'cannot connect to %s: %s' % (self.hostname, e.args[1])
		except IOError, e:
			if e.errno == errno.ENOENT:
				raise SSHError, 'No key file available.'
			raise


	def close(self):
		for channel in self.channels:
			channel.close()
		if self.ssh:
			pass #self.ssh.close()
		self.ssh = None


	def rcmd(self, args, shell=False, pty=False, userepo=True):
		# To manage metainformation, all remote commands run
		# through self.remotecmd (connect client --server-mode).
		#
		# If shell=False, then args are arguments to connect
		# client.  If shell=True, then connect client executes
		# them as a shell command.
		opts = []

		if userepo and self.repo:
			# insert a --repo option
			opts += ['--repo', self.repo]

		if shell:
			# shrun (s_shrun) is how we run shell commands
			args = ['shrun'] + args

		# sequence and quote the command for paramiko
		args = self.remotecmd + opts + args
		cmd = ' '.join([quote(arg) for arg in args])

		channel = self.transport.open_session()
		self.debug('client command: ' + cmd)
		if pty:
			term = os.environ.get('TERM', 'vt100')
			rows, cols = ttysize()
			channel.get_pty(term=term, width=cols, height=rows)
		channel.exec_command(cmd)
		channel.fp = channel.makefile()
		self.channels.append(channel)

		# Set some additional methods on the channel object
		# for convenience to the receiver.

		def _(sig, action):
			'''SIGWINCH handler'''
			rows, cols = ttysize()
			channel.resize_pty(width=cols, height=rows)
		channel.winch = _

		def _(**kwargs):
			'''remote i/o proxy'''
			return self.rio(channel, **kwargs)
		channel.rio = _

		def _(message, code, **kwargs):
			'''protocol message exchange proxy'''
			return self.exchange(channel, message, code, **kwargs)
		channel.exchange = _

		def _(*args, **kwargs):
			'''protocol command proxy'''
			return self.pcmd(channel, *args, **kwargs)
		channel.pcmd = _

		def _(*args, **kwargs):
			'''protocol response receiver proxy'''
			return self.pgetline(channel, *args, **kwargs)
		channel.pgetline = _

		def _(*args, **kwargs):
			'''protocol response reply proxy'''
			return self.preply(channel, *args, **kwargs)
		channel.preply = _

		return channel


	def handshake(self):
		if self.isdebug:
                        channel = self.rcmd(['server', '--debug'], shell=False)
		else:
			channel = self.rcmd(['server'], shell=False)
		banner = channel.pgetline()
		if not banner.startswith('connect client protocol'):
			channel.close()
			raise SSHError, 'no connect sync server at server endpoint (closing)'

		self.version = int(banner.split()[3])
		channel.session = self
		return channel


	def rio(self, channel, stdin=True, stdout=True, stderr=True):
		'''I/O loop for remote command channels.'''
		# XXX may need to extend to support filters, etc.

		events = {}

		if stdin:
			def _():
				data = sys.stdin.read(1024)
				channel.send(data)
				return len(data)
			events[sys.stdin.fileno()] = _

		if stdout or stderr:
			def _():
				bytes = 0
				if stdout and channel.recv_ready():
					data = channel.recv(1024)
					sys.stdout.write(data)
					bytes += len(data)
				if stderr and channel.recv_stderr_ready():
					data = channel.recv_stderr(1024)
					sys.stderr.write(data)
					bytes += len(data)
				return bytes
			events[channel.fileno()] = _

		poll = select.poll()
		for fd in events:
			poll.register(fd, select.POLLIN)

		ready = True
		while ready:
			for fd, event in poll.poll():
				if event == select.POLLIN:
					if events[fd]() == 0:
						ready = False
						break
			sys.stdout.flush()


	def exchange(self, channel, message, responses):
		channel.pcmd(message)
		data = []
		until = None
		while True:
			sys.stdout.flush()
			line = channel.pgetline()
			if until:
				if line == until:
					until = None
					return data
				else:
					data.append(line)
				continue

			args = line.split()
			rcode = int(args.pop(0))

			# Responses may be a dict or an int.
			#
			# If responses is an int, it's a termination condition. If
			# the code matches it, we return the args and iteration of
			# the exchange is interrupted.
			#
			# If responses is a dict and rcode is a key in responses,
			# the value should be a callable that accepts a list of
			# args, and returns a (bool, list) tuple. The list replaces
			# args, and if the bool is true then the exchange halts.
			# Otherwise the exchange continues iterating.
			#
			# Alternatively the value may be None; in this case the
			# args are returned and the exchange is interrupted.
			#
			# In yet another option that I just thought of, the value
			# may be an Exception instance, in which case it will be raised.
			if rcode == responses:
				return args
			elif hasattr(responses, '__getitem__') and rcode in responses:
				if responses[rcode] and isinstance(responses[rcode], Exception):
					raise responses[rcode]
				elif responses[rcode]:
					stop, args = responses[rcode](args)
					if stop:
						return args
				else:
					return args
			elif rcode == codes.MULTILINE:
				if args:
					until = args[0]
				else:
					until = '.'
			else:
				raise SSHError, 'unexpected response %d %s' % (rcode, ' '.join(args))


	def pcmd(self, channel, *args):
		msg = ' '.join([str(x) for x in args])
		self.debug('>> ' + msg)
		return channel.send(msg + '\n')


	def pgetline(self, channel, split=False):
		msg = None
		while not msg:
			msg = channel.fp.readline()
			if msg == '':
				raise IOError, 'end of stream'
			msg = msg.strip()
			self.debug('<< ' + msg)

		if split:
			return msg.split()
		return msg


	def preply(self, channel, code, args):
		# are we even going to use this? not sure.
		return self.pcmd(channel, str(code), *args)


	def sftp(self):
		return paramiko.SFTPClient.from_transport(self.transport)


class Profile(object):
	def __init__(self, *args, **kwargs):
		self._name = None
		self._user = None
		self._server = None

		if args:
			self.split(args[0])

		for k, v in kwargs.items():
			setattr(self, k, v)

	def __str__(self):
		return '[%s: user=%s, server=%s]' % \
		       (self.name, self.user, self.server)

	@property
	def name(self):
		if self._name:
			return self._name
		return self.join()

	@name.setter
	def name(self, value):
		self._name = value

	@property
	def user(self):
		return self._user

	@user.setter
	def user(self, value):
		self._user = value
		self._invalidate()

	@property
	def server(self):
		return self._server

	@server.setter
	def server(self, value):
		self._server = value
		self._invalidate()

	def _invalidate(self):
		if self._name and '@' in self._name:
			self._name = None

	def split(self, value):
		if '@' in value:
			self.user, self.server = value.strip().split('@', 1)
			# leave it alone if already set
			#if self.user == '':
			#	self.user = None
		else:
			self.user = value
			# leave it alone if already set
			#self.server = None


	def join(self):
		if self.user and self.server:
			return self.user + '@' + self.server
		elif self.user:
			return self.user
		elif self.server:
			return '@' + self.server
		else:
			raise InvalidProfile, 'no user, server, or name in profile'


	@classmethod
	def fromconfig(cls, cfg):
		profiles = {}
		if not cfg.has_section('clientprofiles'):
			return profiles

		defaults = cfg.defaults()
		for option, value in cfg.items('clientprofiles'):
			if option in defaults:
				continue
			if value == '':
				value = option
			p = Profile()
			p.split(value)
			p.name = option
			profiles[p.name] = p

		return profiles


	def toconfig(self, *args):
		if args:
			cfg = args[0]
		else:
			cfg = ConfigParser.ConfigParser()

		if not cfg.has_section('clientprofiles'):
			cfg.add_section('clientprofiles')

		value = self.join()
		option = self.name
		cfg.set('clientprofiles', option, value)

		return cfg


class main(object):
	def secret(f):
		'''decorator to make a client command secret'''
		f.secret = True
		return f

	def clientcmd(shorts, longs):
		'''decorator to make a function a client subcommand with
		pre-parsed options.'''
		def _outer(f):
			def _inner(self, opts, args, **kwargs):
				sopts = shorts
				lopts = longs
				sopts += 'hd'
				lopts += ['help', 'debug']
				try:
					nopts, nargs = getopt.getopt(args, sopts, lopts)
				except getopt.GetoptError, e:
					self.error(e)
					return 2
				for opt, arg in nopts:
					if opt in ('-d', '--debug'):
						self.isdebug = True
						self.debug = self._debug
					if opt in ('-h', '--help'):
						self.usage(commands=[f.__name__.replace('c_', '')])
						return 2
					else:
						opts += [(opt, arg)]
				args = nargs
				return f(self, opts, args, **kwargs)
			_inner.__doc__ = f.__doc__
			return _inner
		return _outer

	# server commands aren't really any different
	servercmd = clientcmd

	@property
	def joburl(self):
		return 'connect://%s@%s/%s' % (self.profile.user, self.profile.server, self.repo)


	def mkjuid(self):
		sha = hashlib.sha1()
		sha.update(self.repo)
		sha.update(str(time.time()))
		hashstr = ''.join(['%02x' % ord(c) for c in sha.digest()[:4]])
		return self.repo + '-' + hashstr


	def __init__(self):
		self.name = os.path.basename(sys.argv[0])
		self.opts = []
		self.args = []
		self.mode = 'client'
		self.keybits = 2048
		self.session = None
		self.repo = None #os.path.basename(os.getcwd())
		self.implicit = True
		self.juid = None

		self.showsecret = False
		self.debug = lambda *args: True
		self.isdebug = False
		self.idletimeout = 5 * 60
		self.verbose = False

		self.rows, self.cols = ttysize()

		if config.getboolean('connect', 'client'):
			self.local = os.path.basename(sys.argv[0])
		else:
			self.local = ' '.join([os.path.basename(sys.argv[0]), __name__])

		# We'll put all the user/server contextual information
		# into a profile object:
		self.profile = Profile(name=None, server=None, user=None)

		# try to load local config (it may not exist)
		self.lconfig = ConfigParser.ConfigParser()
		self.lconfig.read(os.path.expanduser('~/.connect/client.ini'))
		self.lconfig.read('.connect/config.ini')
		mergeconfig(config, self.lconfig, overwrite=True)
		profiles = Profile.fromconfig(config)

		# check for preferred profile
		if self.lconfig.has_section('client'):
			if self.lconfig.has_option('client', 'profile'):
				self.profile = profiles[self.lconfig.get('client', 'profile')]
			elif self.lconfig.has_option('client', 'lastprofile'):
				self.profile = profiles[self.lconfig.get('client', 'lastprofile')]

		if not self.profile.server and not self.profile.user and 'default' in profiles:
			self.profile = profiles['default']
			self.profile.name = None

		# Go through some options for updating the de facto profile.

		if not self.profile.server:
			self.profile.server = DEFAULT_CLIENT_SERVER

		if not self.profile.user:
			self.profile.user = os.environ.get('USER') or getpass.getuser()

		if 'CONNECT_CLIENT_SERVER' in os.environ:
			self.profile.server = os.environ.get('CONNECT_CLIENT_SERVER')

		if 'CONNECT_CLIENT_USER' in os.environ:
			self.profile.user = os.environ.get('CONNECT_CLIENT_USER')

		# end profile stuff


	def _msg(self, fp, prefix, *args, **kwargs):
		if 'indent' in kwargs and kwargs['indent']:
			prefix = ' ' * len(prefix)

		if len(args) > 1:
			text = prefix + args[0] % args[1:]
		else:
			text = prefix + str(args[0])
		if 'wrap' in kwargs and kwargs['wrap']:
			text = textwrap.fill(text, width=self.cols*0.9)
		print >>fp, text
		fp.flush()

	def error(self, *args, **kwargs):
		return self._msg(sys.stderr, 'error: ', *args, **kwargs)

	def notice(self, *args, **kwargs):
		return self._msg(sys.stdout, 'notice: ', *args, **kwargs)

	def _debug(self, *args, **kwargs):
		return self._msg(sys.stderr, '%s: ' % self.mode, *args, **kwargs)
	debug = lambda *args: True

	def output(self, *args, **kwargs):
		if 'wrap' not in kwargs:
			kwargs['wrap'] = True
		return self._msg(sys.stdout, '', *args, **kwargs)

	def _example_deco_without_args(f):
		def _(self, args):
			print self.name, args
			return f(self, args)
		return _

	def _example_deco_with_args(_args):
		def _(f):
			def _(self, args):
				print self.name, _args, args
				return f(self, args)
			return _
		return _

	def decorator(f):
		return f


	def hostname(self):
		return socket.gethostname()


	def ensure_dir(self, path, mode=0700):
		try:
			os.makedirs(path, mode)
		except:
			pass


	def path(self, *args):
		path = os.path.join(*args)
		if not os.path.isabs(path):
			path = os.path.join(os.path.expanduser('~'), path)
		return path


	def makeident(self):
		return self.profile.user + '@' + self.profile.server


	def ssh_keygen(self, ident=None, comment=None):
		import StringIO

		if not ident:
			ident = self.makeident()

		if not comment:
			comment = '(connect)'

		rsa = paramiko.rsakey.RSAKey.generate(self.keybits)
		fp = StringIO.StringIO()
		rsa.write_private_key(fp)
		key = fp.getvalue()
		fp.close()

		pub = 'ssh-rsa ' + rsa.get_base64() + ' ' + ident + ' ' + comment
		return ident, key, pub


	def unlink(self, file):
		try:
			os.unlink(file)
		except:
			pass


	def savefile(self, file, content, overwrite=False, mode=0600):
		fp = tempfile.NamedTemporaryFile(dir=os.path.dirname(file), delete=False)
		fp.write(content)
		fp.close()
		if not overwrite and os.path.exists(file):
			raise IOError, '"%s" exists' % file
		os.rename(fp.name, file)


	def readfile(self, file):
		fp = open(file, 'r')
		data = fp.read()
		fp.close()
		return data


	def keyfile(self, ident=None):
		if not ident:
			ident = self.makeident()
		return os.path.expanduser(os.path.join('~/.ssh/connect', ident))


	def sessionsetup(self):
		try:
			return ClientSession(self.profile.server, user=self.profile.user,
			                     keyfile=self.keyfile(),
			                     repo=os.path.basename(os.getcwd()),
			                     password='nopassword', debug=self.debug)
		except SSHError, e:
			e.bubble(
			    'You have no access to %s.' % self.profile.server,
			)


	def platforminfo(self):
		print '| Connect client version:', _version
		print '| Python version:', sys.version.replace('\n', '\n|   ')
		print '| Prefix:', sys.prefix
		print '| User profile:', self.profile
		print
		print 'System:'

		def sh(cmd):
			fp = os.popen(cmd, 'r')
			for line in fp:
				sys.stdout.write('| ' + line)
			fp.close()
		sh('uname -a')
		sh('uptime')
		sys.stdout.flush()


	def disabled_c_prototest(self, args):
		''''''

		session = self.sessionsetup()
		channel = session.handshake()

		local = os.getcwd()
		remote = os.path.basename(local)

		channel.exchange('dir %s create=yes' % remote, codes.OK)

		for i in xrange(5):
			x = random.randint(0, 100)
			channel.exchange('ping %d' % x, codes.OK)

		#data = channel.exchange('multitest foo', codes.OK)
		#print data

		# Offer some files
		for filename in ['ahoy.txt', 'xxatlas.sum']:
			# Initiate a push
			s = os.lstat(filename)
			channel.pcmd('want %s mtime=%d size=%d' % (self.fnencode(filename), s.st_mtime, s.st_size))
			args = channel.pgetline(split=True)
			rcode = int(args.pop(0))
			if rcode == codes.YES:
				# send
				pass

		# Request file list from server, and individual files
		data = channel.exchange('list', codes.OK)
		for line in data:
			args = line.strip().split()
			fn = self.fndecode(args.pop(0))
			attrs = self.attrs(args)
			if self.needfile(fn, attrs):
				# request file
				pass

		channel.exchange('quit', codes.OK)


	def chdir(self, dir):
		self.debug('chdir(%s)' % dir)
		os.chdir(dir)


	def push(self, channel, verbose=False, noop=False, timings=False):
		mdcache = set()
		def awfulrecursivemkdir(sftp, dir):
			if dir in mdcache:
				return
			rel = '.'
			for part in dir.split('/'):
				rel = os.path.join(rel, part)
				try:
					rs = sftp.stat(rel)
				except:
					sftp.mkdir(rel)
			mdcache.add(dir)

		if verbose:
			wanted = self.notice
			unwanted = self.notice
			error = self.notice
		else:
			def wanted(*args):
				sys.stdout.write('+')
				sys.stdout.flush()
			def unwanted(*args):
				sys.stdout.write('.')
				sys.stdout.flush()
			def error(*args):
				sys.stdout.write('!')
				sys.stdout.flush()

		start = time.time()
		cumulative = 0.0
		size = 0.0

		channel.exchange('dir %s create=yes' % self.repo, codes.OK)
		sftp = channel.session.sftp()

		servercwd, = channel.exchange('getcwd', codes.OK)
		sftp.chdir(servercwd)

		sent = 0
		unsent = 0
		errors = 0
		for root, dirs, files in os.walk('.'):
			for file in files + dirs:
				fn = os.path.join(root, file)
				fn = cleanfn(fn)
				# Initiate a push
				s = os.lstat(fn)
				#rfn = os.path.join(self.repo, fn)
				rfn = fn

				if stat.S_ISDIR(s.st_mode):
					channel.pcmd('want %s mtime=%d mode=0%04o' % (self.fnencode(fn), s.st_mtime, s.st_mode & 07777))
					args = channel.pgetline(split=True)
					rcode = int(args.pop(0))

					if rcode == codes.YES:
						# send
						awfulrecursivemkdir(sftp, os.path.dirname(rfn))

						try:
							wanted('sending %s/...', fn)
							rs = sftp.stat(rfn)
						except:
							try:
								if not noop:
									sftp.mkdir(rfn)
								sent += 1
							except:
								error('cannot send %s/...', fn)
								errors += 1
					else:
						unwanted('not sending %s/...', fn)
						unsent += 1

				else:
					channel.pcmd('want %s mtime=%d size=%d mode=0%04o' % (self.fnencode(fn), s.st_mtime, s.st_size, s.st_mode & 07777))
					args = channel.pgetline(split=True)
					rcode = int(args.pop(0))

					if rcode == codes.YES:
						# send
						if not noop:
							awfulrecursivemkdir(sftp, os.path.dirname(rfn))

						try:
							wanted('sending %s...', fn)
							if not noop:
								_ = time.time()
								sftp.put(fn, rfn)
								cumulative += time.time() - _
								size += s.st_size
							sent += 1
						except Exception, e:
							error('error sending %s: %s', rfn, str(e))
							errors += 1

					else:
						unwanted('not sending %s...', fn)
						unsent += 1

				if not noop:
					sftp.utime(rfn, (s.st_atime, s.st_mtime))
					sftp.chmod(rfn, s.st_mode)
					# do we need this? doesn't utime() handle it?
					#channel.exchange('stime %s %d' % (self.fnencode(fn), s.st_mtime), codes.OK)

		end = time.time()

		if not verbose:
			sys.stdout.write('\n')
		note = noop and '(no-op) ' or ''
		self.output('%s%d objects sent; %d objects up to date; %d errors',
		            note, sent, unsent, errors)
		if timings:
			if cumulative:
				self.output('time: real %.3fs, net %.3fs, rate %.3fK/s' %
				            (end - start, cumulative, size / (1024*cumulative)))
			else:
				self.output('time: real %.3fs, net %.3fs' %
				            (end - start, cumulative))
		sys.stdout.flush()


	def pull(self, channel, verbose=False, noop=False, timings=False):
		if verbose:
			wanted = self.notice
			unwanted = self.notice
			error = self.notice
		else:
			def wanted(*args):
				sys.stdout.write('+')
				sys.stdout.flush()
			def unwanted(*args):
				sys.stdout.write('.')
				sys.stdout.flush()
			def error(*args):
				sys.stdout.write('!')
				sys.stdout.flush()

		start = time.time()
		cumulative = 0.0
		size = 0.0

		channel.exchange('dir %s' % self.repo, codes.OK)
		sftp = channel.session.sftp()

		servercwd, = channel.exchange('getcwd', codes.OK)
		sftp.chdir(servercwd)

		# Request file list from server, and individual files
		if self.implicit:
			cmd = 'list'
		else:
			cmd = 'list %s' % self.fnencode(servercwd)
		data = channel.exchange(cmd, {
			codes.OK: None,
			codes.NOTPRESENT: NotPresentError('%s not found' % self.joburl),
		})

		sent = 0
		unsent = 0
		error = 0
		for line in data:
			args = line.strip().split()
			fn = self.fndecode(args.pop(0))
			attrs = self.attrs(args)
			if self.needfile(fn, attrs):
				# request file
				#rfn = os.path.join(self.repo, fn)
				rfn = fn
				dir = os.path.dirname(fn)
				wanted('fetching %s...', rfn)
				if not noop:
					self.ensure_dir(dir)
					_ = time.time()
					sftp.get(rfn, fn)
					cumulative += time.time() - _
					s = os.stat(fn)
					size += s.st_size
					if 'mtime' in attrs:
						t = int(attrs['mtime'])
						os.utime(fn, (t, t))
				sent += 1
			else:
				rfn = fn
				unwanted('not fetching %s...', rfn)
				unsent += 1

		end = time.time()

		if not verbose:
			sys.stdout.write('\n')
		note = noop and '(no-op) ' or ''
		self.output('%s%d objects retrieved; %d objects up to date; %d errors',
					note, sent, unsent, error)
		if timings:
			if cumulative:
				self.output('time: real %.3fs, net %.3fs, rate %.3fK/s' %
				            (end - start, cumulative, size / (1024*cumulative)))
			else:
				self.output('time: real %.3fs, net %.3fs' %
				            (end - start, cumulative))
		sys.stdout.flush()


	def sreply(self, code, *args):
		msg = str(code) + ' ' + ' '.join(args)
		self.debug('server: >> ' + msg)
		sys.stdout.write(msg + '\n')
		sys.stdout.flush()


	def attrs(self, args):
		attrs = {}
		for arg in args:
			if '=' not in arg:
				continue
			prop, val = arg.split('=', 1)
			attrs[prop.lower()] = val
		return attrs


	def needfile(self, file, attrs):
		try:
			s = os.lstat(file)
		except:
			return True

		if 'size' in attrs and s.st_size != int(attrs['size']):
			self.debug('wanted: size %d != %s' % (s.st_size, attrs['size']))
			return True

		if 'mtime' in attrs and s.st_mtime < int(attrs['mtime']):
			self.debug('wanted: mtime %d != %s' % (s.st_mtime, attrs['mtime']))
			return True

		mode = s.st_mode & 07777
		if 'mode' in attrs and mode != int(attrs['mode'], base=8):
			self.debug('wanted: mode %o != %s' % (mode, attrs['mode']))
			return True

		return False


	def fnencode(self, fn):
		return urllib.quote_plus(fn)


	def fndecode(self, fn):
		return urllib.unquote_plus(fn)


	def usage(self, commands=[]):
		self.output('This is Connect Client %s.' % _version)
		for line in self._help(commands=commands):
			if line.startswith('@ '):
				line = 'usage: %s %s' % (self.local, line[2:])
			self.output(line)

	def _help(self, commands=[]):
		yield '@ [opts] <subcommand> [args]'
		for attr in sorted(dir(self)):
			if attr.startswith('c_'):
				subcmd = attr[2:]
				if commands and subcmd not in commands:
					continue
				driver = getattr(self, attr)
				if hasattr(driver, 'secret') and driver.secret:
					if self.showsecret:
						yield '      -%s [opts] %s %s' % (self.local, subcmd, driver.__doc__)
				else:
					yield '       %s [opts] %s %s' % (self.local, subcmd, driver.__doc__)

		yield ''
		yield 'opts:'
		yield '    -s|--server hostname       set connect server name'
		yield '    -u|--user username         set connect server user name'
		yield '    -r|--remote directory      set connect server directory name'
		yield '    -v|--verbose               show additional information'


	def saveconf(self, config, file=None):
		if file:
			file = os.path.expanduser(file)
			dir = os.path.dirname(file)
		else:
			dir = os.path.join(self.repodir, '.connect')
			file = os.path.join(dir, 'config.ini')
		self.ensure_dir(dir)
		try:
			fp = open(file, 'w')
			config.write(fp)
			fp.close()
			return True
		except:
			return False


	def __call__(self, args):
		args = list(args)
		try:
			self.opts, self.args = getopt.getopt(args, 'u:ds:r:vh',
			        ['server-mode', 'user=', 'debug', 'server=',
			         'show-secret', 'repo=', 'verbose', 'help'])
		except getopt.GetoptError, e:
			self.error(e)
			return 2

		for opt, arg in self.opts:
			if opt in ('--server-mode',):
				self.mode = 'server'

			if opt in ('--show-secret',):
				self.showsecret = True

			if opt in ('-u', '--user'):
				self.profile.user = arg

			if opt in ('-d', '--debug'):
				self.isdebug = True

			if opt in ('-s', '--server'):
				self.profile.server = arg

			if opt in ('-v', '--verbose'):
				self.verbose = True

			if opt in ('-r', '--repo'):
				self.setrepo(arg)
				self.checkjuid(create=True)

			if opt in ('-h', '--help'):
				self.usage()
				return 0

		if self.isdebug:
			self.debug = self._debug

		if self.verbose:
			self.output('\nAdditional information:')
			self.platforminfo()
			print

		if self.mode != 'server':
			self.createaliases(cacheonly=True)

		if len(self.args) == 0:
			self.usage()
			return 2

		if self.mode != 'server' and not hasattr(paramiko, '__file__'):
			self.error('%s %s requires the "paramiko" module for python%d.%d',
			           self.name, __name__, sys.version_info[0], sys.version_info[1])
			self.error('(try "pip install paramiko")')
			if paramiko:
				# paramiko is an exception or string indicating error
				self.debug(paramiko)
			sys.exit(5)

		# Update alias stubs
		if self.mode != 'server':
			self.createaliases(cacheonly=False)

		subcmd = self.args.pop(0)
		if self.mode == 'client':
			driver = 'c_' + subcmd
		else:
			driver = 's_' + subcmd

		try:
			driver = getattr(self, driver)
		except AttributeError:
			self.error('"%s" is not a valid subcommand. (Try %s -h.)',
			           subcmd, self.local)
			return 10

		if self.mode == 'server' and self.repo is None:
			## server mode with no --repo is an error
			#self.error('no job repository was specified')
			#return 30
			self.repodir = os.getcwd()
			self.repo = os.path.basename(self.repodir)

		elif self.mode == 'client' and self.repo is None:
			# client, no repo dir specified. Use cwd.
			self.setrepo()
			# checkjuid will load juid if present, or leave blank
			# if not, deferring creation to push/pull operation.
			self.checkjuid()

		try:
			rc = driver(self.opts, self.args)
		except SSHError, e:
			e.bubble('Did you run "%s setup"?' % self.local)
		except UsageError, e:
			e.bubble('usage: %s %s %s' % (self.local, subcmd, driver.__doc__))

		# save final user profile
		self.profile.toconfig(self.lconfig)
		if not self.lconfig.has_section('client'):
			self.lconfig.add_section('client')
		self.lconfig.set('client', 'lastprofile', self.profile.name)
		self.saveconf(self.lconfig)

		if self.session:
			self.session.close()
		return rc


	def setrepo(self, *args):
		if args:
			self.repo = args[0]

		if self.mode == 'server':
			# basedir must only be used by server
			self.basedir = config.get('server', 'staging')
			if self.repo:
				self.repodir = os.path.join(self.basedir, self.repo)
				self.ensure_dir(self.repodir)

			else:
                                self.repo = "/stash/user/{0}/connect-client".format(self.profile.user)
				# no declared repo; error
				#raise NoRepoError, 'no repository was declared'

		else:
			# client
			if self.repo and os.path.exists(self.repo):
				# client --repo was likely used, and is a local path
				os.chdir(self.repo)
				self.repodir = os.getcwd()
				self.repo = os.path.basename(self.repodir)

			elif self.repo:
				# does not exist, client
				self.repodir = os.path.realpath(self.repo)
				self.ensure_dir(self.repodir)

			else:
				# no declared repo; use current dir
				self.repodir = os.getcwd()
				self.repo = os.path.basename(self.repodir)

		self.chdir(self.repodir)


	def checkjuid(self, create=False):
		dir = os.path.join(self.repodir, '.connect')
		file = os.path.join(dir, 'juid')
		try:
			fp = open(file, 'r')
			self.juid = fp.read().strip()
			fp.close()
			return self.juid
		except:
			pass

		if create:
			self.ensure_dir(dir)
			fp = open(file, 'w')
			self.juid = self.mkjuid()
			fp.write(self.juid)
			fp.close()
			return self.juid

		# no juid
		return None


	def _aliascache(self, *args):
		fp = None
		dir = os.path.expanduser('~/.connect/aliases')
		file = os.path.join(dir, self.profile.server)

		if args:
			try:
				# store aliases
				aliases, = args
				self.ensure_dir(dir)
				fp = open(file, 'w')
				json.dump(aliases, fp)
				fp.close()
			except:
				pass
			
		else:
			try:
				# check cache age
				s = os.stat(file)
				if s.st_mtime - time.time() > 86400:
					self.debug('expiring the server alias cache')
					raise Exception, 'cache too old'
				# retrieve aliases
				fp = open(file, 'r')
				aliases = json.load(fp)
				fp.close()
			except:
				aliases = None

		if fp:
			fp.close()
		return aliases


	def _serveraliases(self, cacheonly=False):
		aliases = self._aliascache()
		if aliases is not None:
			return aliases

		# no cached results

		if cacheonly:
			return {}

		try:
			session = self.sessionsetup()
			channel = session.rcmd(['aliases'], shell=False)
			data = ''
			for line in channel.fp:
				data += line
			aliases = json.loads(data)
			self._aliascache(aliases)
		except:
			# Most likely error is paramiko.client.GeneralException
			# But regardless, we don't want to cache failure.
			aliases = {}

		return aliases


	def createaliases(self, cacheonly=False):
		# create stubs for server aliases
		aliases = self._serveraliases(cacheonly=cacheonly)
		for alias in aliases.keys():
			setattr(self, 'c_' + alias,
			        new.instancemethod(self.serveralias(aliases[alias]), self))


	def serveralias(self, alias):
		name = alias['alias']
		help = alias['help']
		usage = alias['usage']
		def _(self, opts, args):
			session = ClientSession(self.profile.server,
			                        user=self.profile.user,
			                        keyfile=self.keyfile(),
			                        password='nopassword',
			                        repo=os.path.basename(os.getcwd()),
			                        debug=self.debug)

			if self.repo is None:
				self.repo = os.path.basename(os.getcwd())

			channel = session.rcmd(['runalias', name] + args, shell=False, pty=True)
			channel.rio()
			rc = channel.recv_exit_status()
			session.close()
			return rc
		_.__doc__ = usage
		_.isalias = True
		if alias.get('secret'):
			_.secret = True
		return _


	@clientcmd('', [])
	def c_version(self, opts, args):
		''''''
		self.output('Client information:')
		self.platforminfo()
		print


	@secret
	@clientcmd('', [])
	def c_aliases(self, opts, args):
		''''''
		aliases = self._serveraliases()

		for alias in sorted(aliases.keys()):
			if aliases[alias]['secret']:
				continue
			print '%s %s' % (aliases[alias]['alias'], aliases[alias]['usage'])
			print '  -', aliases[alias]['help']
			print


	def _readaliases(self, args, action=True):
		if not config.has_section('server-alias'):
			return
		data = {}
		aliases = config.options('server-alias')
		aliases = [x[:-6] for x in aliases if x.endswith('.alias')]
		for alias in aliases:
			item = {'alias': alias}

			try:
				item['help'] = config.get('server-alias', alias + '.help')
			except:
				item['help'] = ''

			try:
				item['usage'] = config.get('server-alias', alias + '.usage')
			except:
				item['usage'] = ''

			try:
				item['secret'] = config.getboolean('server-alias', alias + '.secret')
			except:
				item['secret'] = False

			if action:
				item['action'] = config.get('server-alias', alias + '.alias')
			data[alias] = item
		return data


	@servercmd('', [])
	def s_aliases(self, opts, args):
		aliases = self._readaliases(args, action=False)
		print json.dumps(aliases)


	@servercmd('', [])
	def s_runalias(self, opts, args):
		aliases = self._readaliases(args, action=True)
		action = aliases[args[0]]['action']
		cmd = ' '.join([action] + args[1:])
		os.system(cmd)


	@clientcmd('', ['replace-keys', 'update-keys'])
	def c_setup(self, opts, args):
		'''[--replace-keys] [--update-keys] [user][@servername]'''

		overwrite = False
		update = False

		for opt, arg in opts:
			if opt in ('--replace-keys',):
				overwrite = True
			if opt in ('--update-keys',):
				update = True

		if args:
			self.profile.split(args.pop(0))
		else:
			self.output('Please enter the user name that you created during Connect registration.  When you visit http://osgconnect.net/ and log in, your user name appears in the upper right corner: note that it consists only of letters and numbers, with no @ symbol.')
			self.output('')
			self.output('You will be connecting via the %s server.' % self.profile.server)
			try:
				user = raw_input('Enter your Connect username: ')
				if user == '':
					self.output('')
					self.output('Setup cancelled.')
					return 0
			except:
				self.output('')
				self.output('')
				self.output('Setup cancelled.')
				return 0

			self.profile.user = user

		self.ensure_dir(self.path('.ssh/connect'))
		ident, key, pub = self.ssh_keygen()
		keyfile = self.keyfile()
		pubfile = keyfile + '.pub'

		if os.path.exists(keyfile) and os.path.exists(pubfile) and not overwrite:
			self.notice('You already have a setup key. (You may wish to run')
			self.notice('"%s setup --replace-keys" .)', self.local)
			return 20

		# If either pubfile or keyfile exists, it's missing its partner;
		# setting overwrite will fix it.  And if neither is present, overwrite
		# does no harm.
		overwrite = True

		# save this user profile
		pconfig = self.profile.toconfig()
		if not pconfig.has_section('client'):
			pconfig.add_section('client')
		pconfig.set('client', 'lastprofile', self.profile.name)
		self.saveconf(pconfig, file='~/.connect/client.ini')

		# expressly do not use a keyfile (prompt instead)
		try:
			session = ClientSession(self.profile.server,
			                        user=self.profile.user, keyfile=None,
			                        repo=os.path.basename(os.getcwd()),
			                        debug=self.debug)
		except SSHError, e:
			# Not sure of a better way to detect this
			if e.args[0] == 'Client authentication failed':
				self.error('Incorrect password for %s' % self.profile.join())
				return 22
			raise GeneralException, e.args

		channel = session.rcmd(['setup'], shell=False, userepo=False)
		channel.send(pub + '\n')
		channel.send('.\n')
		channel.rio(stdin=False)
		channel.close()

		if update:
			oldkeyfile = self.keyfile().replace(self.profile.server, self.hostname())
			oldpubfile = oldkeyfile + '.pub'
			if os.path.exists(oldkeyfile) and os.path.exists(oldpubfile):
				os.rename(oldkeyfile, keyfile)
				os.rename(oldpubfile, pubfile)
				self.output('Keys updated.')
				return 0
			self.error('No keys could be updated.')
			return 21

		try:
			self.savefile(keyfile, key, overwrite=overwrite)
			self.savefile(pubfile, pub, overwrite=overwrite)
		except IOError, e:
			self.error(e)
			self.error('(You may wish to run "%s setup --replace-keys" .)', self.local)
			return 20

		self.notice('Ongoing client access has been authorized at %s.',
		            self.profile.server)
		self.notice('Use "%s test" to verify access.', self.local)
		return 0


	@servercmd('', [])
	def s_setup(self, opts, args):
		'''--server-mode setup'''
		self.ensure_dir(self.path('.ssh'))
		fn = os.path.join('.ssh', 'authorized_keys')
		if os.path.exists(fn):
			authkeys = self.readfile(fn)
		else:
			authkeys = ''
		nauthkeys = authkeys

		while True:
			line = sys.stdin.readline()
			line = line.strip()
			if line == '.':
				break
			nauthkeys += line + '\n'

		if nauthkeys != authkeys:
			if os.path.exists(fn):
				os.rename(fn, fn + '.save')
			self.savefile(fn, nauthkeys, mode=0600, overwrite=True)

		return 0


	@secret
	@clientcmd('', [])
	def c_echo(self, opts, args):
		''' '''

		session = self.sessionsetup()
		channel = session.rcmd(['echo'], shell=False, userepo=False)
		# we will do an echo test here later. For now, just echo at both ends.
		while True:
			buf = channel.recv(1024)
			if len(buf) <= 0:
				break
			sys.stdout.write(buf)
			sys.stdout.flush()

		return 0


	@servercmd('', [])
	def s_echo(self, opts, args):
		'''Echo everything in a loop.'''
		sys.stdout.write('Echo mode.\n')
		sys.stdout.flush()
		while True:
			buf = sys.stdin.read(1024)
			if len(buf) <= 0:
				break
			sys.stdout.write(buf)
			sys.stdout.flush()


	@clientcmd('', [])
	def c_test(self, opts, args):
		''' '''

		if self.verbose:
			_verbose = 'verbose'
		else:
			_verbose = 'noverbose'

		# XXX TODO does not correctly detect when you can log in remotely,
		# but the client command is missing.
		code = str(random.randint(0, 1000))

		session = self.sessionsetup()
		channel = session.rcmd(['test', code, _verbose], shell=False, userepo=False)
		test = ''
		while True:
			buf = channel.recv(1024)
			if len(buf) <= 0:
				break
			test += buf
		test = [x.strip() for x in test.strip().split('\n')]
		if code != test[0]:
			self.output('You have no access to %s. ' +
			            'Run "%s setup" to begin.', self.profile.server, self.local)
			return 10

		self.output('Success! Your client access to %s is working.', self.profile.server)
		if len(test) > 1:
			self.output('\nAdditional information:')
			for item in test[1:]:
				self.output(' * ' + item)
		return 0


	@servercmd('', [])
	def s_test(self, opts, args):
		'''Just an echo test to verify access to server.
		With verbose, print additional info.'''

		print args[0]
		sys.stdout.flush()
		if 'verbose' in args[1:]:
			self.platforminfo()
		return 0


	@servercmd('', [])
	def s_server(self, opts, args):
		debugfp = None
		if args and args[0] == '-debug':
			debugfp = open(os.path.expanduser('~/connect-server.log'), 'w')
			sys.stderr = debugfp
			def _(*args):
				if not args:
					return
				if '%' in args[0]:
					debugfp.write((args[0] % args[1:]) + '\n')
				else:
					debugfp.write(' '.join([str(x) for x in args]) + '\n')
				debugfp.flush()
			self.debug = _

		# hello banner / protocol magic
		sys.stdout.write('connect client protocol 1\n')
		sys.stdout.flush()

		recvfile = None
		idle = False

		def alrm(sig, ctx):
			idle = True
			sys.stderr.write('idle timeout\n')

		signal.signal(signal.SIGALRM, alrm)

		while not idle:
			# reset idle timer on each loop
			signal.alarm(self.idletimeout)

			line = sys.stdin.readline()
			if line == '':
				self.debug('hangup')
				break
			line = line.strip()
			if line == '':
				continue
			args = line.split()
			cmd = args.pop(0).lower()
			#self.debug('server: <<', cmd, args)

			if cmd == 'quit':
				self.sreply(codes.OK, 'bye')
				break

			elif cmd == 'ping':
				self.sreply(codes.OK, 'pong', args[0])

			elif cmd == 'getcwd':
				# n.b. SFTP has no real notion of a cwd. It can appear
				# to place relative files relative to the process
				# cwd, but it doesn't really. Relative files are located
				# relative to the home dir, no matter what the cwd actually
				# is.
				#
				# To work around this the CLIENT needs to know the server's
				# cwd. This method gives a way to communicate that.
				self.sreply(codes.OK, os.getcwd())

			elif cmd == 'dir':
				dir = cleanfn(args.pop(0))
				attrs = self.attrs(args)
				self.chdir(self.basedir)
				try:
					self.chdir(dir)
					self.sreply(codes.OK, dir, 'ok')
				except:
					if 'create' in attrs and attrs['create'] == 'yes':
						try:
							os.makedirs(dir)
							self.chdir(dir)
							self.sreply(codes.OK, dir, 'created')
						except:
							self.sreply(codes.FAILED, dir, 'cannot create')
					else:
						self.sreply(codes.NOTPRESENT, dir, 'not present')

			elif cmd == 'multitest':
				endtag = 'end'
				self.sreply(codes.MULTILINE, endtag)
				sys.stdout.write('line 1 | %s\n' % line)
				sys.stdout.write('line 2 | %s\n' % line)
				sys.stdout.write('line 3 | %s\n' % line)
				sys.stdout.write('line 4 | %s\n' % line)
				sys.stdout.write(endtag + '\n')

			elif cmd == 'list':
				if args:
					try:
						self.chdir(self.fndecode(args.pop(0)))
					except OSError:
						self.sreply(codes.NOTPRESENT)
						break

				self.sreply(codes.MULTILINE)
				for root, dirs, files in os.walk('.'):
					for file in files:
						fn = os.path.join(root, file)
						fn = cleanfn(fn)
						s = os.lstat(fn)
						if not stat.S_ISREG(s.st_mode):
							continue
						sys.stdout.write('%s size=%d mtime=%d\n' % (
							self.fnencode(fn), s.st_size, s.st_mtime))
				sys.stdout.write('.\n')

			elif cmd == 'want':
				recvfile = cleanfn(self.fndecode(args.pop(0)))
				attrs = self.attrs(args)

				if self.needfile(recvfile, attrs):
					self.sreply(codes.YES, 'yes')
				else:
					self.sreply(codes.NO, 'no')

			elif cmd == 'stime':
				recvfile = cleanfn(self.fndecode(args.pop(0)))
				mtime = int(args[0])
				try:
					os.utime(recvfile, (mtime, mtime))
					self.sreply(codes.OK, '')
				except OSError, e:
					self.sreply(codes.FAILED, str(e))

			else:
				sys.stdout.write('%d unknown command %s\n' % (codes.WAT, cmd))

			sys.stdout.flush()

		sys.stdout.flush()
		if debugfp:
			debugfp.close()
		return 0


	@servercmd('', [])
	def s_shrun(self, opts, args):
		os.environ['HOME'] = self.repodir
		os.environ['JOBREPO'] = self.repo
		os.environ['PS1'] = '%s> ' % self.repo
		cmd = ' '.join([quote(x, chr="'") for x in args])
		proc = subprocess.Popen(cmd, shell=True, stdin=sys.stdin,
		                        stdout=sys.stdout, stderr=sys.stdout)
		proc.communicate()


	#@decorator
	#def sync(f):
	#	'''Decorator that performs file sync before executing
	#	the wrapped fn.
	#	'''
	#	def _(self, args):
	#		self.push()
	#		self.pull()
	#		return f(self, args)
	#	return _


	def _submit(self, args, command='condor_submit'):
		'''<submitfile>'''

		session = self.sessionsetup()

		if self.repo is None:
			self.repo = os.path.basename(os.getcwd())

		# First push all files
		channel = session.handshake()
		self.push(channel)
		channel.exchange('quit', codes.OK)

		# Now run a submit
		channel = session.rcmd([command] + args, shell=True)
		channel.rio()
		rc = channel.recv_exit_status()

		# Now pull all files
		channel = session.handshake()
		self.push(channel)
		channel.exchange('quit', codes.OK)

		# and close
		session.close()
		return rc


	@clientcmd('', [])
	def c_submit(self, opts, args):
		'''<submitfile>'''
		return self._submit(args, command='condor_submit')


	@clientcmd('', [])
	def c_dag(self, opts, args):
		'''<dagfile>'''
		return self._submit(args, command='condor_submit_dag')


	@clientcmd('tnvw', ['time', 'noop', 'verbose', 'where'])
	def c_push(self, opts, args):
		return self._pushpull(opts, args, mode='push')


	@clientcmd('tnvw', ['time', 'noop', 'verbose', 'where'])
	def c_pull(self, opts, args):
		return self._pushpull(opts, args, mode='pull')


	@clientcmd('tnvw', ['time', 'noop', 'verbose', 'where'])
	def c_sync(self, opts, args):
		return self._pushpull(opts, args, mode='sync')


	def _pushpull(self, opts, args, mode=None):
		'''[-t|--time] [-v|--verbose] [-w|--where] [repository-dir]'''

		verbose = False
		where = False
		noop = False
		timings = False

		for opt, arg in opts:
			if opt in ('-v', '--verbose'):
				verbose = True
			if opt in ('-w', '--where'):
				where = True
			if opt in ('-n', '--noop'):
				noop = True
			if opt in ('-t', '--time'):
				timings = True

		if self.isdebug:
			verbose = True

		if len(args) > 1:
			raise UsageError, 'too many arguments'
		elif len(args) == 1:
			self.setrepo(args[0])
			self.checkjuid(create=True)

		if where:
			# don't pull, just show dir path
			return self.c_where([], args)

		session = self.sessionsetup()
		channel = session.handshake()
		if mode == 'pull' or mode == 'sync':
			try:
				self.pull(channel, verbose=verbose, noop=noop, timings=timings)
			except GeneralException, e:
				self.error(e)
		if mode == 'pull':
			try:
				channel.exchange('quit', codes.OK)
			except GeneralException, e:
				self.error(e)
		if mode == 'push' or mode == 'sync':
			self.push(channel, verbose=verbose, noop=noop, timings=timings)
			channel.exchange('quit', codes.OK)

	c_push.__doc__ = c_pull.__doc__ = c_sync.__doc__ = _pushpull.__doc__


	@clientcmd('', [])
	def c_revoke(self, opts, args):
		''''''
		self.output('')
		self.output('This command -permanently- deletes the key used to authorize')
		self.output('access to your Connect servers from this client. You can')
		self.output('re-establish access using "%s setup". Is this' % self.local)
		yn = self.prompt('what you want [y/N]? ')
		self.output('')
		if yn.lower() not in ['y', 'yes']:
			self.output('Not revoking keys.')
			return

		try:
			os.unlink(self.keyfile())
			os.unlink(self.keyfile() + '.pub')
			self.notice('Key revoked.')
		except:
			self.notice('No keys to revoke!')


	def prompt(self, prompt):
		sys.stdout.write(prompt)
		sys.stdout.flush()
		r = sys.stdin.readline()
		return r.strip()

	# Creates a standard method that runs a remote shell command
	# indicated by _args.
	def _remoteshell(*_args):
		_args = list(_args)
		def _(self, opts, args):
			session = ClientSession(self.profile.server,
			                        user=self.profile.user,
			                        keyfile=self.keyfile(),
			                        password='nopassword',
			                        repo=os.path.basename(os.getcwd()),
			                        debug=self.debug)

			if self.repo is None:
				self.repo = os.path.basename(os.getcwd())

			channel = session.rcmd(_args + args, shell=True)
			channel.rio()
			rc = channel.recv_exit_status()
			session.close()
			return rc
		_.__doc__ = '<' + ' '.join(_args) + ' arguments>'
		return _

	# Creates a standard method that runs a remote connnect command.
	def _remoteconnect(*_args, **kwargs):
		min = None
		max = None
		opts = ''
		if 'min' in kwargs:
			min = kwargs['min']
		if 'max' in kwargs:
			max = kwargs['max']
		# TODO: opts should be more getopty
		if 'opts' in kwargs:
			opts = kwargs['opts']
		_args = list(_args)
		def _(self, opts, args):
			if min and len(args) < min:
				raise UsageError, 'not enough arguments'
			if max and len(args) > max:
				raise UsageError, 'too many arguments'

			session = ClientSession(self.profile.server,
			                        user=self.profile.user,
			                        keyfile=self.keyfile(),
			                        password='nopassword',
			                        repo=os.path.basename(os.getcwd()),
			                        debug=self.debug)

			if self.repo is None:
				self.repo = os.path.basename(os.getcwd())

			channel = session.rcmd(_args + ['--'] + args, shell=False)
			channel.rio()
			rc = channel.recv_exit_status()
			session.close()
			return rc
		_.__doc__ = opts
		if 'secret' in kwargs:
			_.secret = kwargs['secret']
		return _

	@clientcmd('', [])
	def c_shell(self, opts, args):
		'''[command]'''
		import termios
		import tty
		import select

		ldisc = termios.tcgetattr(sys.stdin.fileno())
		session = self.sessionsetup()
		interactive = False
		if not args:
			args = ['/bin/sh', '-i']
			interactive = True

		term = open('/dev/tty', 'w')
		channel = session.rcmd(args, shell=True, pty=True)

		# set a SIGWINCH handler to propagate terminal resizes to server
		signal.signal(signal.SIGWINCH, channel.winch)

		if interactive:
			term.write('\n[connected to %s; ^D to disconnect]\n' % self.joburl)
			term.flush()

		try:
			tty.setraw(sys.stdin.fileno())
			tty.setcbreak(sys.stdin.fileno())
			channel.settimeout(0.0)

			while True:
				try:
					rset, wset, eset = select.select([channel, sys.stdin], [], [])
					if channel in rset:
						try:
							buf = channel.recv(1024)
							if len(buf) == 0:
								break
							sys.stdout.write(buf)
							sys.stdout.flush()
						except socket.timeout:
							pass
					if sys.stdin in rset:
						buf = sys.stdin.read(1)
						if len(buf) == 0:
							break
						channel.send(buf)
				except select.error, e:
					if e.args[0] != errno.EINTR:
						raise

		finally:
			termios.tcsetattr(sys.stdin, termios.TCSADRAIN, ldisc)

		signal.signal(signal.SIGWINCH, signal.SIG_DFL)
		if interactive:
			term.write('\n[disconnected from %s]\n' % self.joburl)
			term.flush()
		return


	# These are simple, transparent commands -- no more complexity
	# than 'ssh server cmd args'.
	c_q = _remoteshell('condor_q')
	c_rm = _remoteshell('condor_rm')
	c_history = _remoteshell('condor_history')
	c_run = _remoteshell('condor_run')
	c_wait = _remoteshell('condor_wait')

	# XXX need to store default pool name in local config for status
	c_status = _remoteshell('condor_status')

	# These are direct remote procedure calls to server-mode methods.
	# E.g., if c_xyz = _remoteconnect('abc') then 'connect client xyz'
	# will invoke s_xyz() at the server.
	c_list = _remoteconnect('list', opts='[-v]')
	c_where = _remoteconnect('where', max=0, secret=True)
	c_rconfig = _remoteconnect('rconfig', max=0, secret=True)


	@servercmd('', [])
	def s_list(self, opts, args):
		# List job repos in this dir
		# TODO: should check for job uuid (juid)
		# TODO: some interactive logic to flag out-of-sync repos

		def getsize(path):
			size = 0
			nfiles = 0
			for root, dirs, files in os.walk(path):
				nfiles += len(files)
				for file in files:
					s = os.stat(os.path.join(root, file))
					size += s.st_size
			return nfiles, size

		for entry in sorted(os.listdir(self.basedir)):
			path = os.path.join(self.basedir, entry)
			if entry.startswith('.'):
				continue
			if os.path.islink(path):
				continue
			if not os.path.isdir(path):
				continue
			if '-v' in args:
				nfiles, size = getsize(path)
				print '%s   [%d files, %s total]' % (entry, nfiles, units(size))
			else:
				print entry
			sys.stdout.flush()


	@servercmd('', [])
	def s_where(self, opts, args):
		print self.repodir


	@servercmd('', [])
	def s_rconfig(self, opts, args):
		config.write(sys.stdout)


# consider using rsync implementation by Isis Lovecruft at
# https://github.com/isislovecruft/pyrsync

def run(*args, **kwargs):
	m = main()
	try:
		sys.exit(m(args))

	except KeyboardInterrupt:
		print '\nbreak'
		sys.exit(1)

	except Exception, e:
		if isinstance(e, IOError) and e.errno == errno.EPIPE:
			sys.exit(0)

		if m.isdebug:
			raise
		#m.error('%s ("%s --debug" to diagnose):',
		#           e.__class__.__name__, m.local)
		#for i, arg in enumerate(e.args):
		#	m.error(arg, indent=(i>0))
		m.error(e.__class__.__name__ + ': ' + str(e.args[0]))
		for arg in e.args[1:]:
			m.error(arg)
		sys.exit(10)


try:
	# Paramiko may be built using an older libgmp, but we can't
	# do anything about that.  Suppress this warning.
	import warnings
	with warnings.catch_warnings():
		warnings.simplefilter("ignore")
		import paramiko
except ImportError, e:
	paramiko = e


if __name__ == '__main__':
	run(*sys.argv[1:])
