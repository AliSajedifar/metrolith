// Package demo does things.
package demo

import "strings"

// Service handles requests.
type Service struct {
	Marker string
}

func (s *Service) Handle(request string) string {
	raw := `// not a comment
still raw`
	return strings.Join([]string{s.Marker, request, raw}, "")
}

func Helper() int { return 1 }
