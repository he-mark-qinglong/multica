//go:build !windows

package main

import "syscall"

// diskFreeBytesForPath returns bytes available to unprivileged users on the
// filesystem holding path. Mirrors the syscall usage already in
// cmd_daemon_unix.go — no new dependencies.
func diskFreeBytesForPath(path string) (uint64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(path, &st); err != nil {
		return 0, err
	}
	return uint64(st.Bavail) * uint64(st.Bsize), nil
}
