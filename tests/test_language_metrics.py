import tempfile
import unittest
from pathlib import Path

from modules.endpoints import extract_endpoints
from modules.metric_warnings import assess_metric_warnings
from modules.static_analysis import perform_static_analysis


class LanguageMetricFixtures(unittest.TestCase):
    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_javascript_exact_counts_and_routes(self):
        repo = self._repo()
        (repo / "app.js").write_text(
            """
class Service {
  constructor() {}
  async load() {}
  get value() { return 1; }
  static build() {}
}
function declared() {}
async function fetcher() {}
const expression = function () {};
module.exports = function () {};
exports.named = async function named() {};
const arrow = () => 1;
export const exported = async value => value;
const actions = {
  run() {},
  async stop() {}
};
app.get("/users", (req, res) => res.send([]));
router.route("/items").put(update).delete(remove);
fastify.route({ method: "PATCH", url: "/fast", handler });
""",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 1)
        # Contract 3 adds the two stable CommonJS exports to the main total.
        self.assertEqual(result["methods_functions"], 12)
        self.assertEqual(
            {(ep["method"], ep["route"]) for ep in extract_endpoints(repo)},
            {
                ("GET", "/users"),
                ("PUT", "/items"),
                ("DELETE", "/items"),
                ("PATCH", "/fast"),
            },
        )

    def test_typescript_exact_counts_and_routes(self):
        repo = self._repo()
        (repo / "controller.ts").write_text(
            """
interface User { id: string }
interface Callback { handler: (value: string) => void }
enum Role { Admin, User }
type Identifier = string;
type Handler = (value: string) => void;
class Controller {
  constructor() {}
  async handle(): Promise<void> {}
}
export function create(): void {}
export default function remove(): void {}
export const update = async (id: string) => id;
const parse = function (value: string) { return value; };
const handlers = { validate(value: string) { return value; } };
router.post("/users", update);
""",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 1)
        self.assertEqual(result["methods_functions"], 6)
        self.assertEqual(result["metrics"]["by_language"]["typescript"]["interfaces"], 2)
        self.assertEqual(result["metrics"]["by_language"]["typescript"]["enums"], 1)
        self.assertEqual(result["metrics"]["by_language"]["typescript"]["type_aliases"], 2)
        endpoints = extract_endpoints(repo)
        self.assertEqual(
            [(ep["method"], ep["route"]) for ep in endpoints],
            [("POST", "/users")],
        )

    def test_python_exact_counts_async_and_routes(self):
        repo = self._repo()
        (repo / "views.py").write_text(
            """
class Service:
    def run(self):
        return 1

    async def load(self):
        return 2

class ItemView:
    def get(self):
        return 3

def helper():
    return 4

async def background():
    return 5

@app.route("/items", methods=["GET", "POST"])
def items():
    return []

@blueprint.route("/blue")
async def blue():
    return []

@router.patch("/items/{item_id}")
async def patch_item(item_id: str):
    return item_id
""",
            encoding="utf-8",
        )
        (repo / "urls.py").write_text(
            'urlpatterns = [path("dashboard/", ItemView.as_view())]\n',
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 2)
        self.assertEqual(result["methods_functions"], 8)
        self.assertEqual(
            {(ep["method"], ep["route"]) for ep in extract_endpoints(repo)},
            {
                ("GET", "/items"),
                ("POST", "/items"),
                ("GET", "/blue"),
                ("PATCH", "/items/{item_id}"),
                ("ANY", "dashboard/"),
            },
        )

    def test_go_exact_counts_and_routes(self):
        repo = self._repo()
        (repo / "main.go").write_text(
            """
package main
type User struct { ID string }
type Store interface { Find(string) User }
func Load() {}
func (u *User) Save() {}
func routes() {
    http.HandleFunc("/health", health)
    mux.HandleFunc("/users", users)
    r.GET("/gin", ginHandler)
    e.POST("/echo", echoHandler)
    group.PUT("/group", groupHandler)
    chi.Get("/chi", chiHandler)
}
""",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 1)
        self.assertEqual(result["metrics"]["by_language"]["go"]["interfaces"], 1)
        self.assertEqual(result["methods_functions"], 3)
        self.assertEqual(
            {(ep["method"], ep["route"]) for ep in extract_endpoints(repo)},
            {
                ("ANY", "/health"),
                ("ANY", "/users"),
                ("GET", "/gin"),
                ("POST", "/echo"),
                ("PUT", "/group"),
                ("GET", "/chi"),
            },
        )
        self.assertEqual(result["go_metric_detection_status"], "metrics_detected")
        self.assertEqual(result["go_metric_confidence"], "high")

    def test_go_microservice_patterns_and_audit_evidence(self):
        repo = self._repo()
        for directory in ("cmd/api", "internal/service", "pkg/http", "vendor/lib"):
            (repo / directory).mkdir(parents=True)
        (repo / "cmd/api/main.go").write_text(
            """
package main
func main() {}
func NewService[T any](value T) *Service[T] { return nil }
""",
            encoding="utf-8",
        )
        (repo / "internal/service/service.go").write_text(
            """
package service
type Service[T any] struct { value T }
type Repository interface { Find(string) error }
type (
    Request struct { ID string }
    Port interface { Close() error }
)
func (s *Service[T]) Create() error { return nil }
func (h Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {}
""",
            encoding="utf-8",
        )
        (repo / "pkg/http/routes.go").write_text(
            """
package transport
func Register() {
    http.HandleFunc("/health", health)
    mux.HandleFunc("/users", users)
    r.GET("/gin", ginHandler)
    e.POST("/echo", echoHandler)
    group.DELETE("/group", deleteHandler)
    chiRouter.Patch("/chi", patchHandler)
}
""",
            encoding="utf-8",
        )
        (repo / "vendor/lib/dependency.go").write_text(
            "package lib\ntype Dependency struct{}\nfunc Run() {}\n",
            encoding="utf-8",
        )
        (repo / "internal/service/api.pb.go").write_text(
            "package service\ntype Generated struct{}\nfunc GeneratedFunc() {}\n",
            encoding="utf-8",
        )
        (repo / "internal/service/service_test.go").write_text(
            "package service\ntype TestOnly struct{}\nfunc TestCreate() {}\n",
            encoding="utf-8",
        )

        result = perform_static_analysis(repo)

        self.assertEqual(result["classes_structs"], 2)
        self.assertEqual(result["metrics"]["by_language"]["go"]["interfaces"], 2)
        self.assertEqual(result["methods_functions"], 5)
        self.assertEqual(result["go_scanned_files"], 3)
        self.assertEqual(result["go_skipped_generated_files"], 1)
        self.assertEqual(result["go_skipped_dependency_files"], 0)
        self.assertEqual(result["go_pruned_dependency_directories"], 1)
        self.assertEqual(result["go_skipped_test_files"], 1)
        self.assertEqual(result["go_parse_failed_files"], 0)
        self.assertEqual(result["go_metric_detection_status"], "metrics_detected")
        self.assertEqual(
            {(ep["method"], ep["route"]) for ep in extract_endpoints(repo)},
            {
                ("ANY", "/health"),
                ("ANY", "/users"),
                ("GET", "/gin"),
                ("POST", "/echo"),
                ("DELETE", "/group"),
                ("PATCH", "/chi"),
            },
        )

    def test_go_zero_and_parser_failure_statuses_are_explainable(self):
        no_structs = self._repo()
        (no_structs / "main.go").write_text(
            "package main\nfunc main() {}\n", encoding="utf-8"
        )
        result = perform_static_analysis(no_structs)
        self.assertEqual(
            result["go_metric_detection_status"], "checked_no_structs_found"
        )
        self.assertEqual(
            result["go_struct_detection_status"], "checked_no_structs_found"
        )

        no_functions = self._repo()
        (no_functions / "models.go").write_text(
            "package models\ntype User struct { ID string }\n", encoding="utf-8"
        )
        result = perform_static_analysis(no_functions)
        self.assertEqual(result["go_metric_detection_status"], "suspicious_zero")
        self.assertEqual(result["go_function_detection_status"], "suspicious_zero")

        broken = self._repo()
        (broken / "broken.go").write_text(
            "package broken\ntype User struct {\n", encoding="utf-8"
        )
        result = perform_static_analysis(broken)
        self.assertEqual(result["go_metric_detection_status"], "syntax_partial")
        self.assertEqual(result["go_error_categories"], ["syntax_partial"])
        self.assertEqual(result["go_parse_failed_files"], 1)
        self.assertEqual(result["metric_extraction_status"], "partial")
        self.assertEqual(result["classes_structs"], 0)
        self.assertEqual(result["methods_functions"], 0)

    def test_java_exact_counts_spring_and_jax_rs(self):
        repo = self._repo()
        (repo / "UsersController.java").write_text(
            """
@RequestMapping("/api")
class UsersController {
    UsersController() {}
    @GetMapping("/users")
    public List<User> list() { return null; }
    @PostMapping(path = "/users")
    public User create() { return null; }
    private void helper() {}
}
interface UserRepository {
    User find(String id);
}
enum Status { ACTIVE }
record User(String id) {}

@Path("/jax")
class JaxResource {
    @GET
    @Path("/users")
    public Object users() { return null; }
}
""",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["classes_structs"], 3)
        self.assertEqual(result["methods_functions"], 4)
        self.assertEqual(result["metrics"]["by_language"]["java"]["interfaces"], 1)
        self.assertEqual(result["metrics"]["by_language"]["java"]["enums"], 1)
        self.assertEqual(
            {(ep["method"], ep["route"]) for ep in extract_endpoints(repo)},
            {
                ("GET", "/api/users"),
                ("POST", "/api/users"),
                ("GET", "/jax/users"),
            },
        )

    def test_filtering_excludes_tests_dependencies_generated_and_bundles(self):
        repo = self._repo()
        for directory in ("src", "tests", "node_modules", "dist", "vendor", "generated"):
            (repo / directory).mkdir()
        (repo / "src" / "app.js").write_text(
            "class App {}\nconst run = () => 1;\n", encoding="utf-8"
        )
        (repo / "tests" / "app.test.js").write_text(
            "class TestOnly {}\nconst testFn = () => 1;\n", encoding="utf-8"
        )
        (repo / "node_modules" / "dep.js").write_text(
            "class Dependency {}\n", encoding="utf-8"
        )
        (repo / "dist" / "bundle.js").write_text(
            "class Built {}\n", encoding="utf-8"
        )
        (repo / "vendor" / "vendored.go").write_text(
            "package x\ntype Vendored struct{}\n", encoding="utf-8"
        )
        (repo / "generated" / "Generated.java").write_text(
            "class Generated {}\n", encoding="utf-8"
        )
        (repo / "generated-sources").mkdir()
        (repo / "generated-sources" / "MoreGenerated.java").write_text(
            "class MoreGenerated {}\n", encoding="utf-8"
        )
        (repo / "src" / "api.pb.go").write_text(
            "package x\ntype Proto struct{}\nfunc Generated(){}\n", encoding="utf-8"
        )
        (repo / "src" / "zz_generated.deepcopy.go").write_text(
            "package x\ntype DeepCopy struct{}\n", encoding="utf-8"
        )
        (repo / "src" / "app.min.js").write_text(
            "class Minified{};const x=()=>1;", encoding="utf-8"
        )
        (repo / "wwwroot" / "assets" / "plugins").mkdir(parents=True)
        (repo / "wwwroot" / "assets" / "plugins" / "jquery.js").write_text(
            "class VendoredPlugin {}\nconst callback = () => 1;\n",
            encoding="utf-8",
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["source_files"], 1)
        self.assertEqual(result["classes_structs"], 1)
        self.assertEqual(result["methods_functions"], 1)

    def test_python_migrations_are_excluded_from_production(self):
        repo = self._repo()
        (repo / "app").mkdir()
        (repo / "app" / "migrations").mkdir()
        (repo / "app" / "models.py").write_text(
            "class User:\n    pass\n", encoding="utf-8"
        )
        (repo / "app" / "migrations" / "0001_initial.py").write_text(
            "class Migration:\n    pass\n", encoding="utf-8"
        )
        (repo / "app" / "migrations" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["source_files"], 1)
        self.assertEqual(result["classes_structs"], 1)

    def test_testdata_conftest_and_generated_sources_are_excluded(self):
        repo = self._repo()
        (repo / "src").mkdir()
        (repo / "testdata").mkdir()
        (repo / "src" / "app.py").write_text(
            "def run():\n    return 1\n", encoding="utf-8"
        )
        (repo / "conftest.py").write_text(
            "def fixture_helper():\n    return 1\n", encoding="utf-8"
        )
        (repo / "testdata" / "fixture.go").write_text(
            "package fixture\nfunc Fixture() {}\n", encoding="utf-8"
        )
        (repo / "src" / "client.generated.ts").write_text(
            "export class GeneratedClient {}\n", encoding="utf-8"
        )

        result = perform_static_analysis(repo)

        self.assertEqual(result["source_files"], 1)
        self.assertEqual(result["methods_functions"], 1)

    def test_commented_routes_are_not_reported(self):
        repo = self._repo()
        (repo / "routes.go").write_text(
            """
package routes
// r.GET("/commented", handler)
/* http.HandleFunc("/blocked", handler) */
func Register() { r.GET("/real", handler) }
""",
            encoding="utf-8",
        )
        (repo / "routes.py").write_text(
            '# @app.get("/commented-python")\n'
            '@app.get("/real-python")\n'
            "def real():\n    return None\n",
            encoding="utf-8",
        )
        (repo / "Routes.java").write_text(
            "// @GetMapping(\"/commented-java\")\n"
            "class Routes {\n"
            '  @GetMapping("/real-java") public void real() {}\n'
            "}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            {(endpoint["method"], endpoint["route"]) for endpoint in extract_endpoints(repo)},
            {
                ("GET", "/real"),
                ("GET", "/real-python"),
                ("GET", "/real-java"),
            },
        )

    def test_suspicious_metric_warnings(self):
        warning = assess_metric_warnings(
            {
                "source_files": 60,
                "loc": 1000,
                "classes_structs": 0,
                "methods_functions": 0,
                "parse_failure_count": 2,
            },
            [],
            ["express"],
        )
        self.assertTrue(warning["metric_warning"])
        self.assertIn("zero detected methods/functions", warning["metric_warning_reason"])
        self.assertIn("zero endpoints", warning["metric_warning_reason"])
        self.assertIn("could not be confidently parsed", warning["metric_warning_reason"])

    def test_small_functional_repository_does_not_warn_for_zero_classes(self):
        warning = assess_metric_warnings(
            {
                "source_files": 4,
                "loc": 100,
                "classes_structs": 0,
                "methods_functions": 8,
                "parse_failure_count": 0,
            },
            [],
            [],
        )
        self.assertFalse(warning["metric_warning"])

    def test_unparseable_python_reports_unavailable_not_zero(self):
        repo = self._repo()
        (repo / "broken.py").write_text(
            "def broken(:\n    pass\n", encoding="utf-8"
        )
        result = perform_static_analysis(repo)
        self.assertEqual(result["metric_extraction_status"], "failed")
        self.assertIsNone(result["classes_structs"])
        self.assertIsNone(result["methods_functions"])
        warning = assess_metric_warnings(result, [], [])
        self.assertTrue(warning["metric_warning"])
        self.assertIn("could not be confidently parsed", warning["metric_warning_reason"])


if __name__ == "__main__":
    unittest.main()
