// Authorization policies for the knowledge base (DESIGN.md §4).
// The same file would be loaded into Amazon Verified Permissions in production.
//
// Principal: Employee  { email, country, role ("employee" | "hr"), hr_scope ("country" | "global") }
// Resource:  Asset     { audience ("public" | "staff" | "hr" | "hr_global" | "owner_only"), country, classification, owner }

// Public content is readable by every employee.
permit (principal is Employee, action == Action::"read", resource is Asset)
when { resource.audience == "public" };

// Staff content: content for the employee's own country, or global content.
permit (principal is Employee, action == Action::"read", resource is Asset)
when {
  resource.audience == "staff" &&
  (resource.country == "GLOBAL" || resource.country == principal.country)
};

// HR staff read staff and HR content for their country; global HR reads all of it.
permit (principal is Employee, action == Action::"read", resource is Asset)
when {
  principal.role == "hr" &&
  (resource.audience == "staff" || resource.audience == "hr") &&
  (principal.hr_scope == "global" ||
   resource.country == principal.country ||
   resource.country == "GLOBAL")
};

// Documents shared only with the global HR group.
permit (principal is Employee, action == Action::"read", resource is Asset)
when {
  resource.audience == "hr_global" && principal.role == "hr" && principal.hr_scope == "global"
};

// Deny-by-default fallback: when a source permission could not be mapped with
// certainty, only the content owner can read it (DESIGN.md §2).
permit (principal is Employee, action == Action::"read", resource is Asset)
when { resource.audience == "owner_only" && resource.owner == principal.email };

// Confidential content is limited to HR, whatever any other rule says.
forbid (principal is Employee, action, resource is Asset)
when { resource.classification == "confidential" && principal.role != "hr" };

// Restricted content is never served through agents in this POC.
forbid (principal, action, resource is Asset)
when { resource.classification == "restricted" };
