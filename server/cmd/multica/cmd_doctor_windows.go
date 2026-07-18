//go:build windows

package main

import "fmt"

// diskFreeBytesForPath is not implemented for Windows; doctor reports the
// disk check as WARN/skipped there rather than failing the build.
func diskFreeBytesForPath(path string) (uint64, error) {
	return 0, fmt.Errorf("disk-free check not supported on windows")
}
