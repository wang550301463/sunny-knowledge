package schema

type EntityKind string

const (
	EntityService    EntityKind = "Service"
	EntityModule     EntityKind = "Module"
	EntityFile       EntityKind = "File"
	EntityPerson     EntityKind = "Person"
	EntityDependency EntityKind = "Dependency"
	EntityDecision   EntityKind = "Decision"
	EntityPolicy     EntityKind = "Policy"
	EntityIncident   EntityKind = "Incident"
	EntityChange     EntityKind = "Change"
	EntityProcedure  EntityKind = "Procedure"
)

var entityKinds = map[EntityKind]struct{}{
	EntityService: {}, EntityModule: {}, EntityFile: {}, EntityPerson: {},
	EntityDependency: {}, EntityDecision: {}, EntityPolicy: {},
	EntityIncident: {}, EntityChange: {}, EntityProcedure: {},
}

func IsEntityKind(s string) bool {
	_, ok := entityKinds[EntityKind(s)]
	return ok
}

type EdgeKind string

const (
	EdgeDependsOn   EdgeKind = "depends_on"
	EdgeUses        EdgeKind = "uses"
	EdgeOwns        EdgeKind = "owns"
	EdgeCites       EdgeKind = "cites"
	EdgeGovernedBy  EdgeKind = "governed_by"
	EdgeCaused      EdgeKind = "caused"
	EdgeFixed       EdgeKind = "fixed"
	EdgeSupersedes  EdgeKind = "supersedes"
	EdgeContradicts EdgeKind = "contradicts"
)

var edgeKinds = map[EdgeKind]struct{}{
	EdgeDependsOn: {}, EdgeUses: {}, EdgeOwns: {}, EdgeCites: {},
	EdgeGovernedBy: {}, EdgeCaused: {}, EdgeFixed: {},
	EdgeSupersedes: {}, EdgeContradicts: {},
}

func IsEdgeKind(s string) bool {
	_, ok := edgeKinds[EdgeKind(s)]
	return ok
}

const DefaultGroupID = "enterprise"
