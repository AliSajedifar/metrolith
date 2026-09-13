// Independent Go complexity reference, using go/parser and go/ast.
//
// ArchLens uses tree-sitter-go, so the two share no grammar, no parser, no
// traversal and no rule table. That is the only reason a disagreement here
// would mean anything. Parsing only: no type checking, nothing resolved.
//
// Implemented from docs/COMPLEXITY_CONTRACT_V1.md and the hand-authored
// synthetic expectations. No ArchLens metric output was consulted.
//
// Definitions implemented -- ArchLens's, independently:
//
//	population    package-level FuncDecl with a body. Function literals are
//	              EXCLUDED, matching the canonical methods_functions population;
//	              this is deliberately not broadened to every callable.
//
//	decisions     §7.1: each if; each for/range; each non-default case of an
//	              expression switch, type switch or select. `default` and `else`
//	              contribute nothing.
//
//	booleans      §7.2: && and || operator occurrences.
//
//	maxCondition  §11: the largest operator count inside a SINGLE decision
//	              expression.
//
//	nesting       §10: bodies of if/for under a control construct, plus a
//	              switch/select and each of its arms. A bare block does not
//	              nest. A func literal is a boundary and is not traversed.
//
//	nloc          §8: physical lines in the declaration span, minus blank and
//	              comment-only lines.
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
	"strings"
)

// adapterVersion is versioned independently of the Go toolchain AND of the
// sibling adapters: the adapter's RULES or its serialized contract can change
// while the parser stays fixed, and a reader must be able to tell which moved.
//
// 2.1.0 corrects the serialized contract: a valid file with no canonical
// callable now emits `"callables": []` rather than `null`. Go's json.Marshal
// renders a nil slice as null, which forced every consumer to special-case a
// legitimately empty result -- and a consumer that forgot crashed instead of
// reading zero.
const adapterVersion = "2.1.0"

type callableRecord struct {
	Name                      string `json:"name"`
	QualifiedName             string `json:"qualified_name"`
	CallableKind              string `json:"callable_kind"`
	StartLine                 int    `json:"start_line"`
	EndLine                   int    `json:"end_line"`
	NLOC                      int    `json:"nloc"`
	FormalParameterCount      int    `json:"formal_parameter_count"`
	DecisionPointCount        int    `json:"decision_point_count"`
	BooleanOperatorCount      int    `json:"boolean_operator_count"`
	MaxConditionOperatorCount int    `json:"max_condition_operator_count"`
	MaxNestingDepth           int    `json:"max_nesting_depth"`
	CyclomaticComplexity      int    `json:"cyclomatic_complexity"`
}

type fileResult struct {
	Path           string           `json:"path"`
	AdapterVersion string           `json:"adapter_version"`
	Callables      []callableRecord `json:"callables"`
	ExcludedFuncLi int              `json:"excluded_func_literals"`
	ExcludedNoBody int              `json:"excluded_bodyless_declarations"`
}

type metrics struct {
	decisions    int
	booleans     int
	maxCondition int
	maxDepth     int
}

// countBooleans returns && and || occurrences in one expression, stopping at a
// func literal boundary.
func countBooleans(node ast.Node) int {
	total := 0
	ast.Inspect(node, func(n ast.Node) bool {
		if _, isLit := n.(*ast.FuncLit); isLit {
			return false // boundary: its operators belong to no record
		}
		if binary, ok := n.(*ast.BinaryExpr); ok {
			if binary.Op == token.LAND || binary.Op == token.LOR {
				total++
			}
		}
		return true
	})
	return total
}

func (m *metrics) condition(expr ast.Expr) {
	if expr == nil {
		return
	}
	if found := countBooleans(expr); found > m.maxCondition {
		m.maxCondition = found
	}
}

func (m *metrics) note(depth int) {
	if depth > m.maxDepth {
		m.maxDepth = depth
	}
}

// walkStmt descends one statement. `depth` is the nesting level of this
// statement itself; bodies it opens are measured at depth+1.
func (m *metrics) walkStmt(stmt ast.Stmt, depth int) {
	switch node := stmt.(type) {
	case nil:
		return

	case *ast.IfStmt:
		m.decisions++
		m.condition(node.Cond)
		m.booleans += countBooleans(node.Cond)
		m.walkBlock(node.Body, depth+1)
		// `else if` is an *ast.IfStmt in Else, NOT a block, so it stays at this
		// depth and the chain measures flat. A real `else` block nests.
		switch alternative := node.Else.(type) {
		case *ast.BlockStmt:
			m.walkBlock(alternative, depth+1)
		case *ast.IfStmt:
			m.walkStmt(alternative, depth)
		}

	case *ast.ForStmt:
		m.decisions++
		m.condition(node.Cond)
		if node.Cond != nil {
			m.booleans += countBooleans(node.Cond)
		}
		m.walkBlock(node.Body, depth+1)

	case *ast.RangeStmt:
		m.decisions++
		m.walkBlock(node.Body, depth+1)

	case *ast.SwitchStmt:
		m.walkCases(node.Body, depth)
		if node.Tag != nil {
			m.booleans += countBooleans(node.Tag)
		}

	case *ast.TypeSwitchStmt:
		m.walkCases(node.Body, depth)

	case *ast.SelectStmt:
		m.walkCases(node.Body, depth)

	case *ast.BlockStmt:
		// A bare block is not attached to a control construct and opens no
		// nesting level (§10).
		m.walkBlock(node, depth)

	case *ast.LabeledStmt:
		m.walkStmt(node.Stmt, depth)

	case *ast.ExprStmt:
		m.expression(node.X)

	case *ast.AssignStmt:
		for _, expr := range node.Rhs {
			m.expression(expr)
		}

	case *ast.ReturnStmt:
		for _, expr := range node.Results {
			m.expression(expr)
		}

	case *ast.DeferStmt:
		m.expression(node.Call)

	case *ast.GoStmt:
		m.expression(node.Call)

	default:
		// Any other statement contributes only through its expressions.
		ast.Inspect(stmt, func(n ast.Node) bool {
			if _, isLit := n.(*ast.FuncLit); isLit {
				return false
			}
			if binary, ok := n.(*ast.BinaryExpr); ok {
				if binary.Op == token.LAND || binary.Op == token.LOR {
					m.booleans++
				}
			}
			return true
		})
	}
}

// expression counts operators in an expression, excluding func literals.
func (m *metrics) expression(expr ast.Expr) {
	if expr == nil {
		return
	}
	m.booleans += countBooleans(expr)
}

func (m *metrics) walkBlock(block *ast.BlockStmt, depth int) {
	if block == nil {
		return
	}
	for _, stmt := range block.List {
		m.note(depth)
		m.walkStmt(stmt, depth)
	}
}

// walkCases handles a switch or select: the construct opens one level and each
// arm opens another, so a statement inside an arm sits at depth+2.
func (m *metrics) walkCases(block *ast.BlockStmt, depth int) {
	if block == nil {
		return
	}
	for _, clause := range block.List {
		switch arm := clause.(type) {
		case *ast.CaseClause:
			if len(arm.List) > 0 { // `default` has an empty List and adds nothing
				m.decisions++
				for _, expr := range arm.List {
					m.condition(expr)
					m.booleans += countBooleans(expr)
				}
			}
			for _, stmt := range arm.Body {
				m.note(depth + 2)
				m.walkStmt(stmt, depth+2)
			}
		case *ast.CommClause:
			if arm.Comm != nil { // `default` in a select
				m.decisions++
			}
			for _, stmt := range arm.Body {
				m.note(depth + 2)
				m.walkStmt(stmt, depth+2)
			}
		}
	}
}

// parameterCount implements §9 for Go: count DECLARED NAMES; a declaration with
// no identifier counts as one; a variadic declaration counts as one. The
// receiver is a separate field and never enters the count.
func parameterCount(decl *ast.FuncDecl) int {
	if decl.Type == nil || decl.Type.Params == nil {
		return 0
	}
	total := 0
	for _, field := range decl.Type.Params.List {
		if len(field.Names) == 0 {
			total++
			continue
		}
		total += len(field.Names)
	}
	return total
}

func receiverTypeName(decl *ast.FuncDecl) string {
	if decl.Recv == nil || len(decl.Recv.List) == 0 {
		return ""
	}
	expr := decl.Recv.List[0].Type
	if star, ok := expr.(*ast.StarExpr); ok {
		expr = star.X
	}
	if ident, ok := expr.(*ast.Ident); ok {
		return ident.Name
	}
	if index, ok := expr.(*ast.IndexExpr); ok { // generic receiver
		if ident, ok := index.X.(*ast.Ident); ok {
			return ident.Name
		}
	}
	return ""
}

// maskComments blanks comment bytes, preserving line structure, so a line
// mixing code and a trailing comment still counts as code.
func maskComments(source []byte, file *ast.File, set *token.FileSet) []byte {
	masked := make([]byte, len(source))
	copy(masked, source)
	for _, group := range file.Comments {
		start := set.Position(group.Pos()).Offset
		end := set.Position(group.End()).Offset
		for index := start; index < end && index < len(masked); index++ {
			if masked[index] != '\n' && masked[index] != '\r' {
				masked[index] = ' '
			}
		}
	}
	return masked
}

func countCodeLines(raw, masked []string, start, end int) int {
	total := 0
	for index := start - 1; index < end && index < len(raw); index++ {
		if strings.TrimSpace(raw[index]) != "" && strings.TrimSpace(masked[index]) != "" {
			total++
		}
	}
	return total
}

func analyze(path string) (fileResult, error) {
	source, err := os.ReadFile(path)
	if err != nil {
		return fileResult{}, err
	}
	set := token.NewFileSet()
	file, err := parser.ParseFile(set, path, source, parser.ParseComments)
	if err != nil {
		return fileResult{}, err
	}

	rawLines := strings.Split(string(source), "\n")
	maskedLines := strings.Split(string(maskComments(source, file, set)), "\n")

	// An empty (not nil) slice, so a file with no canonical callable
	// serializes as [] rather than null.
	result := fileResult{
		Path:           path,
		AdapterVersion: adapterVersion,
		Callables:      []callableRecord{},
	}
	ast.Inspect(file, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			result.ExcludedFuncLi++
		}
		return true
	})

	for _, declaration := range file.Decls {
		decl, ok := declaration.(*ast.FuncDecl)
		if !ok {
			continue
		}
		if decl.Body == nil {
			result.ExcludedNoBody++
			continue
		}

		measured := metrics{}
		measured.walkBlock(decl.Body, 0)

		start := set.Position(decl.Pos()).Line
		end := set.Position(decl.End()).Line
		kind := "module_function"
		qualified := decl.Name.Name
		if receiver := receiverTypeName(decl); receiver != "" {
			kind = "receiver_method"
			qualified = receiver + "." + decl.Name.Name
		}

		result.Callables = append(result.Callables, callableRecord{
			Name:                      decl.Name.Name,
			QualifiedName:             qualified,
			CallableKind:              kind,
			StartLine:                 start,
			EndLine:                   end,
			NLOC:                      countCodeLines(rawLines, maskedLines, start, end),
			FormalParameterCount:      parameterCount(decl),
			DecisionPointCount:        measured.decisions,
			BooleanOperatorCount:      measured.booleans,
			MaxConditionOperatorCount: measured.maxCondition,
			MaxNestingDepth:           measured.maxDepth,
			CyclomaticComplexity:      1 + measured.decisions + measured.booleans,
		})
	}
	return result, nil
}

// inputPaths resolves the adapter's inputs. `--list FILE` reads one UTF-8
// path per line, which removes any dependence on the command-line length
// limit; bare paths remain accepted for small ad-hoc runs.
func inputPaths(args []string) ([]string, error) {
	if len(args) == 2 && args[0] == "--list" {
		data, err := os.ReadFile(args[1])
		if err != nil {
			return nil, err
		}
		paths := []string{}
		for _, line := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
			trimmed := strings.TrimSpace(line)
			if trimmed != "" {
				paths = append(paths, trimmed)
			}
		}
		return paths, nil
	}
	return args, nil
}

func main() {
	paths, err := inputPaths(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	results := []fileResult{}
	for _, path := range paths {
		result, err := analyze(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", path, err)
			os.Exit(1)
		}
		results = append(results, result)
	}
	payload := map[string]any{
		"adapter_version": adapterVersion,
		"files":           results,
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(payload); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
