package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
)

func TestRangeResponseUsesNativeElementAndUnknownRangeStaysUnresolved(t *testing.T) {
	for _, test := range []struct {
		iterable   string
		unresolved bool
	}{{"[]string", false}, {"map[string]string", false}, {"Unknown", true}} {
		file, err := parser.ParseFile(token.NewFileSet(), "fixture.go", "package fixture; func run(){ var values "+test.iterable+"; for _, s := range values { platform.JSON(w,200,s) } }", 0)
		if err != nil {
			t.Fatal(err)
		}
		r := &registry{types: map[string]declaration{}, functions: map[string]function{}, schemas: map[string]schema{}}
		// Unknown loop value must shadow this otherwise valid receiver declaration.
		r.types["fixture_Store"] = declaration{&ast.StructType{Fields: &ast.FieldList{}}, "fixture"}
		output := r.outputs("fixture", file.Decls[0].(*ast.FuncDecl).Body)["200"]
		if r.containsUnresolved(output, map[string]bool{}) != test.unresolved {
			t.Fatalf("incorrect range output for %s", test.iterable)
		}
		if !test.unresolved && output.(schema)["type"] != "string" {
			t.Fatal("native element type lost")
		}
	}
}
