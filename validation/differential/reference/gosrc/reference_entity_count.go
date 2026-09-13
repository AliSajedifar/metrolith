// Independent Go entity reference, using go/parser and go/ast.
//
// ArchLens uses tree-sitter-go, so the two share no grammar, no parser and no
// code. Parsing only: no type checking, nothing resolved or synthesized.
//
// Definition implemented -- ArchLens's, independently:
//
//	classes_structs    named struct type declarations. Interfaces and other
//	                   named types are NOT structs and are reported separately.
//
//	methods_functions  package-level function declarations plus methods with a
//	                   receiver, in both cases only where a body is present.
//	                   Function literals (closures) are excluded.
//
// Excluded populations are reported separately so the size of each definitional
// exclusion stays visible as evidence.
//
// Not ground truth. go/parser is a reference front end whose disagreements are
// findings about both sides until adjudicated.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"runtime"
	"strings"
)

type fileResult struct {
	Path             string `json:"path"`
	Types            *int   `json:"types"`
	Methods          *int   `json:"methods"`
	Interfaces       int    `json:"interfaces"`
	OtherNamedTypes  int    `json:"other_named_types"`
	ReceiverMethods  int    `json:"receiver_methods"`
	PackageFunctions int    `json:"package_functions"`
	FunctionLiterals int    `json:"function_literals"`
	BodylessFuncs    int    `json:"bodyless_declarations"`
	Error            string `json:"error,omitempty"`
}

type totals struct {
	Types       int `json:"types"`
	Methods     int `json:"methods"`
	FilesParsed int `json:"files_parsed"`
	FilesFailed int `json:"files_failed"`
}

func analyze(path string) (fileResult, error) {
	result := fileResult{Path: path}
	fileSet := token.NewFileSet()
	// ParseComments keeps the tree faithful; nothing here reads comments.
	parsed, err := parser.ParseFile(fileSet, path, nil, parser.ParseComments)
	if err != nil {
		return result, err
	}

	types := 0
	methods := 0

	ast.Inspect(parsed, func(node ast.Node) bool {
		switch declaration := node.(type) {
		case *ast.TypeSpec:
			switch declaration.Type.(type) {
			case *ast.StructType:
				types++
			case *ast.InterfaceType:
				result.Interfaces++
			default:
				result.OtherNamedTypes++
			}
		case *ast.FuncDecl:
			if declaration.Body == nil {
				result.BodylessFuncs++
				return true
			}
			if declaration.Recv != nil && len(declaration.Recv.List) > 0 {
				result.ReceiverMethods++
			} else {
				result.PackageFunctions++
			}
			methods++
		case *ast.FuncLit:
			result.FunctionLiterals++
		}
		return true
	})

	result.Types = &types
	result.Methods = &methods
	return result, nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: reference_entity_count <file-list>")
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot read file list: %v\n", err)
		os.Exit(3)
	}

	var files []string
	for _, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed != "" {
			files = append(files, trimmed)
		}
	}

	results := make([]fileResult, 0, len(files))
	aggregate := totals{}
	for _, path := range files {
		result, err := analyze(path)
		if err != nil {
			result.Error = err.Error()
			aggregate.FilesFailed++
		} else {
			aggregate.Types += *result.Types
			aggregate.Methods += *result.Methods
			aggregate.FilesParsed++
		}
		results = append(results, result)
	}

	payload := map[string]any{
		"files":      results,
		"totals":     aggregate,
		"go_version": strings.TrimPrefix(runtime.Version(), "go"),
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot encode result: %v\n", err)
		os.Exit(4)
	}
	os.Stdout.Write(encoded)
}
