// contract-export reads Go declarations. It never constructs a service or opens a database.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

type schema = map[string]any
type declaration struct {
	expression ast.Expr
	pkg        string
}
type function struct {
	node        *ast.FuncDecl
	pkg, source string
}
type registry struct {
	types     map[string]declaration
	functions map[string]function
	schemas   map[string]schema
	files     map[string][]*ast.File
	paths     map[string]string
}

func (r *registry) name(pkg string, expression ast.Expr) string {
	switch x := expression.(type) {
	case *ast.Ident:
		return pkg + "_" + x.Name
	case *ast.SelectorExpr:
		if id, ok := x.X.(*ast.Ident); ok {
			return id.Name + "_" + x.Sel.Name
		}
	case *ast.StarExpr:
		return r.name(pkg, x.X)
	}
	return ""
}

func nullable(value schema) schema { return schema{"anyOf": []any{value, schema{"type": "null"}}} }

func (r *registry) shape(pkg string, expression ast.Expr) schema {
	switch x := expression.(type) {
	case *ast.Ident:
		switch x.Name {
		case "string":
			return schema{"type": "string"}
		case "bool":
			return schema{"type": "boolean"}
		case "int", "int32", "int64", "uint", "uint32", "uint64":
			return schema{"type": "integer"}
		case "float32", "float64":
			return schema{"type": "number"}
		case "byte":
			return schema{"type": "integer", "minimum": 0, "maximum": 255}
		case "any":
			return schema{"x-open-json": true}
		}
	case *ast.StarExpr:
		return nullable(r.shape(pkg, x.X))
	case *ast.ArrayType:
		if id, ok := x.Elt.(*ast.Ident); ok && id.Name == "byte" {
			return nullable(schema{"type": "string", "contentEncoding": "base64"})
		}
		value := schema{"type": "array", "items": r.shape(pkg, x.Elt)}
		if x.Len == nil {
			return nullable(value)
		}
		return value
	case *ast.MapType:
		return nullable(schema{"type": "object", "additionalProperties": r.shape(pkg, x.Value)})
	case *ast.InterfaceType:
		return schema{"x-open-json": true}
	case *ast.StructType:
		properties := map[string]any{}
		required := []string{}
		for _, field := range x.Fields.List {
			if len(field.Names) == 0 {
				ref := r.name(pkg, field.Type)
				r.ensure(ref)
				if embedded := r.schemas[ref]; embedded != nil {
					if props, ok := embedded["properties"].(map[string]any); ok {
						for k, v := range props {
							properties[k] = v
						}
					}
					if names, ok := embedded["required"].([]string); ok {
						required = append(required, names...)
					}
				}
				continue
			}
			tag := ""
			if field.Tag != nil {
				decoded, _ := strconv.Unquote(field.Tag.Value)
				tag = reflect.StructTag(decoded).Get("json")
			}
			parts := strings.Split(tag, ",")
			if parts[0] == "-" {
				continue
			}
			for _, name := range field.Names {
				if !name.IsExported() {
					continue
				}
				wireName := parts[0]
				if wireName == "" {
					wireName = name.Name
				}
				value := r.shape(pkg, field.Type)
				properties[wireName] = value
				optional := false
				for _, option := range parts[1:] {
					if option == "omitempty" || option == "omitzero" {
						optional = true
					}
				}
				if !optional {
					required = append(required, wireName)
				}
			}
		}
		sort.Strings(required)
		return schema{"type": "object", "properties": properties, "required": required, "additionalProperties": false}
	case *ast.SelectorExpr:
		if r.name(pkg, x) == "time_Time" {
			return schema{"type": "string", "format": "date-time"}
		}
		if r.name(pkg, x) == "json_RawMessage" {
			return schema{"x-open-json": true}
		}
	}
	name := r.name(pkg, expression)
	if _, ok := r.types[name]; ok {
		r.ensure(name)
		return schema{"$ref": "#/components/schemas/" + name}
	}
	return schema{"x-unresolved-go-type": name}
}

func (r *registry) ensure(name string) {
	if _, ok := r.schemas[name]; ok {
		return
	}
	declaration, ok := r.types[name]
	if !ok {
		return
	}
	r.schemas[name] = schema{}
	value := r.shape(declaration.pkg, declaration.expression)
	value["title"] = name
	value["x-go-declaration"] = declaration.pkg + "." + strings.TrimPrefix(name, declaration.pkg+"_")
	r.schemas[name] = value
}

func evalString(expression ast.Expr, env map[string]string) (string, bool) {
	switch x := expression.(type) {
	case *ast.BasicLit:
		if x.Kind == token.STRING {
			s, e := strconv.Unquote(x.Value)
			return s, e == nil
		}
	case *ast.Ident:
		s, ok := env[x.Name]
		return s, ok
	case *ast.BinaryExpr:
		if x.Op == token.ADD {
			a, ok := evalString(x.X, env)
			b, ok2 := evalString(x.Y, env)
			return a + b, ok && ok2
		}
	}
	return "", false
}

func walk(node ast.Node, env map[string]string, visit func(*ast.CallExpr, map[string]string)) {
	ast.Inspect(node, func(n ast.Node) bool {
		if loop, ok := n.(*ast.RangeStmt); ok {
			values, ok := loop.X.(*ast.CompositeLit)
			id, idOK := loop.Value.(*ast.Ident)
			if ok && idOK {
				for _, v := range values.Elts {
					if s, known := evalString(v, env); known {
						child := map[string]string{}
						for k, v := range env {
							child[k] = v
						}
						child[id.Name] = s
						walk(loop.Body, child, visit)
					}
				}
				return false
			}
		}
		if call, ok := n.(*ast.CallExpr); ok {
			visit(call, env)
		}
		return true
	})
}

func requestType(body *ast.BlockStmt) ast.Expr {
	if body == nil {
		return nil
	}
	variables := map[string]ast.Expr{}
	var result ast.Expr
	ast.Inspect(body, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.ValueSpec:
			if x.Type != nil {
				for _, id := range x.Names {
					variables[id.Name] = x.Type
				}
			}
		case *ast.CallExpr:
			method := ""
			switch f := x.Fun.(type) {
			case *ast.Ident:
				method = f.Name
			case *ast.SelectorExpr:
				method = f.Sel.Name
			}
			if (method == "Decode" || method == "body") && len(x.Args) == 3 {
				if pointer, ok := x.Args[2].(*ast.UnaryExpr); ok {
					if id, ok := pointer.X.(*ast.Ident); ok {
						result = variables[id.Name]
					}
				}
			}
		}
		return true
	})
	return result
}

func operationID(pkg, method, path string) string {
	path = strings.ReplaceAll(path, "{", "by_")
	path = strings.ReplaceAll(path, "}", "")
	var b strings.Builder
	underscore := false
	for _, c := range strings.ToLower(pkg + "_" + method + "_" + path) {
		if c >= 'a' && c <= 'z' || c >= '0' && c <= '9' {
			b.WriteRune(c)
			underscore = false
		} else if !underscore {
			b.WriteByte('_')
			underscore = true
		}
	}
	return strings.Trim(b.String(), "_")
}

func parameters(path string) []any {
	var result = []any{}
	for _, part := range strings.Split(path, "/") {
		if strings.HasPrefix(part, "{") && strings.HasSuffix(part, "}") {
			result = append(result, schema{"name": part[1 : len(part)-1], "in": "path", "required": true, "schema": schema{"type": "string"}})
		}
	}
	return result
}

func (r *registry) routes(pkg string) map[string]any {
	paths := map[string]any{}
	for _, file := range r.files[pkg] {
		for _, decl := range file.Decls {
			function, ok := decl.(*ast.FuncDecl)
			if !ok || function.Body == nil {
				continue
			}
			walk(function.Body, map[string]string{}, func(call *ast.CallExpr, env map[string]string) {
				selector, ok := call.Fun.(*ast.SelectorExpr)
				if !ok || selector.Sel.Name != "HandleFunc" || len(call.Args) != 2 {
					return
				}
				pattern, ok := evalString(call.Args[0], env)
				if !ok {
					panic("Unresolved dynamic route: " + pkg)
				}
				parts := strings.SplitN(pattern, " ", 2)
				if len(parts) != 2 {
					return
				}
				method, path := strings.ToLower(parts[0]), parts[1]
				var body *ast.BlockStmt
				handler := "inline"
				if sel, ok := call.Args[1].(*ast.SelectorExpr); ok {
					handler = sel.Sel.Name
					body = r.functions[pkg+"_Handler_"+handler].node.Body
				} else if f, ok := call.Args[1].(*ast.FuncLit); ok {
					body = f.Body
				}
				operation := schema{"operationId": operationID(pkg, method, path), "x-service": pkg, "x-handler": handler, "x-transport": "http-json", "x-response-typing": "untyped", "x-untyped-reason": "No response schema inferred from this handler yet; no Any-returning client is generated.", "parameters": parameters(path), "responses": schema{"200": schema{"description": "Successful response; exact status and output require handler binding", "content": schema{"application/json": schema{"schema": schema{}}}}}}
				if input := requestType(body); input != nil {
					operation["requestBody"] = schema{"required": true, "content": schema{"application/json": schema{"schema": r.shape(pkg, input)}}}
					operation["x-request-validation"] = "Go JSON shape; imperative handler validation remains authoritative"
				}
				item, ok := paths[path].(map[string]any)
				if !ok {
					item = map[string]any{}
					paths[path] = item
				}
				item[method] = operation
			})
		}
	}
	return paths
}

func main() {
	root := flag.String("root", ".", "repository root")
	flag.Parse()
	r := &registry{types: map[string]declaration{}, functions: map[string]function{}, schemas: map[string]schema{}, files: map[string][]*ast.File{}, paths: map[string]string{}}
	for _, pkg := range []string{"platform", "iam", "auth", "channel", "gateway"} {
		directory := filepath.Join(*root, "services/go/internal", pkg)
		files, err := filepath.Glob(filepath.Join(directory, "*.go"))
		if err != nil {
			panic(err)
		}
		for _, path := range files {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ParseComments)
			if err != nil {
				panic(err)
			}
			r.files[pkg] = append(r.files[pkg], file)
			for _, decl := range file.Decls {
				switch d := decl.(type) {
				case *ast.GenDecl:
					for _, spec := range d.Specs {
						if t, ok := spec.(*ast.TypeSpec); ok {
							r.types[pkg+"_"+t.Name.Name] = declaration{t.Type, pkg}
						}
					}
				case *ast.FuncDecl:
					receiver := ""
					if d.Recv != nil {
						receiver = strings.TrimPrefix(r.name(pkg, d.Recv.List[0].Type), pkg+"_") + "_"
					}
					r.functions[pkg+"_"+receiver+d.Name.Name] = function{d, pkg, path}
				}
			}
		}
	}
	// Only JSON-tagged source structs are wire roots; DB pools/security implementations are not models.
	keys := []string{}
	for k := range r.types {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, name := range keys {
		d := r.types[name]
		if s, ok := d.expression.(*ast.StructType); ok {
			for _, f := range s.Fields.List {
				if f.Tag != nil && strings.Contains(f.Tag.Value, "json:") {
					r.ensure(name)
					break
				}
			}
		}
	}
	result := map[string]any{}
	for _, pkg := range []string{"iam", "auth", "channel"} {
		paths := r.routes(pkg)
		result[pkg] = schema{"openapi": "3.1.0", "info": schema{"title": pkg, "version": "1.0.0"}, "paths": paths, "components": schema{"schemas": r.schemas}, "x-contract-source": "Go AST: JSON struct tags and registered HTTP handlers; no runtime startup"}
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
