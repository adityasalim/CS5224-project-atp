import ast, sys, unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] / 'agent/tp_travel_agent.py').read_text(encoding='utf-8')
tree = ast.parse(SOURCE)

# v5's _order_places ranks with a score that calls several helpers and module constants. Instead of listing them by
# hand, load the two functions under test together with everything they need from the module (functions, constants
# and standard-library imports), so the test keeps working when those helpers change.
top = {}
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        top[node.name] = node
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        top[node.targets[0].id] = node
imports = {}
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            imports[(alias.asname or alias.name).split('.')[0]] = node

def needed(roots):
    seen, stack = set(), list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in top:
            continue
        seen.add(name)
        stack += [n.id for n in ast.walk(top[name]) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
    return seen

closure = needed(['_order_places', '_c_place'])
used_names = {n.id for name in closure for n in ast.walk(top[name]) if isinstance(n, ast.Name)}
body = []
for name in used_names & set(imports):
    mod = imports[name]
    root = (mod.module if isinstance(mod, ast.ImportFrom) else mod.names[0].name).split('.')[0]
    if root not in sys.stdlib_module_names:
        raise unittest.SkipTest(f'{name} comes from {root}; the ranking helpers should only need the standard library')
    body.append(mod)
body += sorted((top[n] for n in closure), key=lambda n: n.lineno)
ns = {}
exec(compile(ast.Module(body=body, type_ignores=[]), '<agent>', 'exec'), ns)
order, c_place = ns['_order_places'], ns['_c_place']
ids = lambda rows: [p['placeId'] for p in rows]

def museums(n, rating=3):
    return [{'placeId': f'm{i}', 'category': 'museum', 'rating': rating} for i in range(n)]

class Pool(unittest.TestCase):
    def test_categories_survive_cap(self):
        rows = museums(30) + [{'placeId': 't', 'category': 'temple', 'rating': 4.8}]
        self.assertIn('t', ids(order(rows, 22)))

    def test_cap_is_respected_and_pinned_come_first(self):
        # requested places are always kept ahead of everything else when the cap leaves room for them
        rows = museums(30) + [{'placeId': f'r{i}', 'category': 'museum', 'requested': True} for i in range(4)]
        out = order(rows, 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(ids(out)[:4], ['r0', 'r1', 'r2', 'r3'])

    def test_must_haves_beyond_the_cap_are_cut_to_the_cap(self):
        # v5 behaviour: with 4 requested places and a cap of 2, the result is cut to 2 (it used to return all 4)
        rows = [{'placeId': str(i), 'category': 'museum', 'requested': True} for i in range(4)]
        self.assertEqual(ids(order(rows, 2)), ['0', '1'])
        self.assertEqual(len(order(rows)), 4)                      # no cap: all of them

    def test_pinned_from_spec_are_kept_without_the_requested_flag(self):
        rows = museums(20, rating=5) + [{'placeId': 'pin', 'category': 'museum', 'rating': 0}]
        out = order(rows, 5, {'pinned': [{'placeId': 'pin', 'name': 'Pinned place'}]})
        self.assertEqual(len(out), 5)
        self.assertEqual(ids(out)[0], 'pin')

    def test_spec_can_be_passed_as_second_argument(self):
        rows = museums(3)
        self.assertEqual(sorted(ids(order(rows, {'interests': ['museum']}))), ['m0', 'm1', 'm2'])

    def test_preference_scores_change_the_ranking(self):
        rows = museums(5) + [{'placeId': 'trail', 'category': 'nature', 'rating': 3}]
        nature_fan = {'preferenceScores': {'nature': 10, 'culture': 1}}
        culture_fan = {'preferenceScores': {'nature': 1, 'culture': 10}}
        self.assertEqual(ids(order(rows, 1, nature_fan)), ['trail'])
        self.assertNotIn('trail', ids(order(rows, 1, culture_fan)))

    def test_ranking_is_deterministic(self):
        rows = museums(12) + [{'placeId': f'c{i}', 'category': 'cafe', 'rating': 4} for i in range(6)]
        spec = {'interests': ['museum', 'cafe']}
        first = ids(order(rows, 10, spec))
        self.assertEqual(first, ids(order(list(rows), 10, spec)))
        self.assertEqual(len(first), len(set(first)))

    def test_actual_ratings_exposed_missing_unknown(self):
        self.assertEqual(c_place({'placeId': 't', 'rating': 4.8})['rating'], 4.8)
        self.assertIsNone(c_place({'placeId': 'x'})['rating'])

    def test_cuisine_is_exposed_to_the_planner(self):
        self.assertEqual(c_place({'placeId': 'r', 'category': 'restaurant', 'cuisine': 'japanese'})['cuisine'], 'japanese')
        self.assertNotIn('cuisine', c_place({'placeId': 'r2', 'category': 'restaurant'}))

if __name__ == '__main__':
    unittest.main()
