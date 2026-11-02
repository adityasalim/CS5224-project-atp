"""Bounded narrative feedback; itinerary repair remains in the verified planning loop."""
import copy

def revise_narrative(story, facts, review, revise):
    original = copy.deepcopy(story)
    try:
        feedback = review(copy.deepcopy(original), copy.deepcopy(facts))
    except Exception:
        return original, {'status': 'review_unavailable', 'target': 'none'}
    if not isinstance(feedback, dict) or type(feedback.get('passed')) is not bool:
        return original, {'status': 'invalid_review', 'target': 'none'}
    if feedback['passed']:
        return original, {'status': 'passed', 'target': 'none'}
    issues = feedback.get('issues')
    if not isinstance(issues, list) or not issues or not all(
        isinstance(x, dict) and x.get('scope') == 'narrative' and
        isinstance(x.get('instruction'), str) and x['instruction'].strip() for x in issues):
        return original, {'status': 'unresolved_itinerary_or_ambiguous_feedback', 'target': 'planner'}
    try:
        candidate = revise(copy.deepcopy(original), copy.deepcopy(facts), copy.deepcopy(issues))
    except Exception:
        return original, {'status': 'revision_unavailable', 'target': 'executor'}
    dates = {d['date'] for d in facts.get('days', [])}
    valid = (isinstance(candidate, dict) and set(candidate) == {'title', 'summary', 'dayNotes'}
             and all(isinstance(candidate.get(k), str) and candidate[k].strip() for k in ('title', 'summary'))
             and isinstance(candidate.get('dayNotes'), dict)
             and set(candidate['dayNotes']).issubset(dates)
             and all(isinstance(v, str) for v in candidate['dayNotes'].values()))
    if not valid:
        return original, {'status': 'invalid_revision', 'target': 'executor'}
    return candidate, {'status': 'revised_not_rejudged', 'target': 'executor'}
