#!/usr/bin/env python

import os
import sys
import string
import curses
import curses.panel
import getopt
import time

def usage():
	yield '@'

# TODO: need a help keystroke

class Watch(object):
	# Loosely based on a solution by 'kalhartt' to
	# http://stackoverflow.com/questions/14200721/how-to-create-a-menu-and-submenus-in-python-curses

	def __init__(self, cmd):
		self.position = 0
		self.cmd = cmd

	def display(self, scr, *args, **kwargs):
		if 'timeout' in kwargs:
			timeout = kwargs['timeout']
		else:
			timeout = 0.0

		result = (None, None)
		if args:
			y, x = args
		else:
			y, x = 0, 0
		window = scr.subwin(y, x)
		window.keypad(1)
		panel = curses.panel.new_panel(window)
		panel.top()
		panel.show()
		window.clear()

		curses.cbreak()
		window.timeout(timeout * 1000)

		while True:
			ts = time.strftime('%a, %d %b %Y at %H:%M:%S')
			scr.addstr(0, 60, ts, curses.A_NORMAL | curses.color_pair(1))
			scr.refresh()
			window.refresh()
			curses.doupdate()
			index = 0
			anything = False
			fp = os.popen(self.cmd, 'r')
			for line in fp:
				line = line.strip()
				if line == '' and not anything:
					continue
				anything = True
				index += 1
				mode = curses.A_NORMAL | curses.color_pair(1)
				msg = line
				try:
					window.addstr(index, 1, msg, mode)
				except curses.error:
					# out of room
					break
			fp.close()


			key = window.getch()
			if key == 27:		# ESCAPE
				break
			elif key == ord('Q'):
				break
			elif key == ord('q'):
				break

		window.clear()
		panel.hide()
		curses.panel.update_panels()
		curses.doupdate()

		return result



def app(scr, user):

	curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
	scr.addstr(0, 0, 'Connect Watch - %s' % user, curses.color_pair(1))
	scr.addstr(1, 0, 'Press ESCAPE or "Q" to quit.', curses.color_pair(1))
	scr.refresh()

	watch = Watch('condor_q %s' % user)

	index, name = watch.display(scr, 2, 2, timeout=2.0)
	return index, name


def error(*args, **kwargs):
	fp = sys.stdout
	if 'fp' in kwargs:
		fp = kwargs['fp']
	print >>fp, os.path.basename(sys.argv[0]) + ': ' + ' '.join(args)


def main(*args):
	job = False

	try:
		opts, args = getopt.getopt(args, 'j:', ['job'])
	except getopt.GetoptError, e:
		return error(str(e))

	for opt, arg in opts:
		if opt == '--job':
			job = True
		elif opt == '-j':
			if arg == 'ob':
				job = True
			else:
				return error('option', '-j' + arg, 'not recognized')

	index, name = curses.wrapper(app, os.environ['USER'])

	return 0


# make this work as a 'connect' extension
run = main

if __name__ == '__main__':
	try:
		sys.exit(main(*sys.argv[1:]))
	except KeyboardInterrupt:
		print '\ninterrupt'
		sys.exit(1)
