# Shared confidence convention

For engineering with Nir and Todd, including Linear discussions and parallel workers, apply confidence scores from 1 to 10 (10 strongest) to substantive claims, criticisms, proposed changes and push reports. Scores inform decisions; they do not replace approval, severity, priority, or merge/test gates.

Calibrate confidence from evidence, relevant expertise and limitations. For agents, include task-specific capability relative to the strongest models currently used; rankings change, so do not hard-code a model hierarchy. Less-capable models should be more cautious about unverified proposals and seek stronger evidence/review. Direct test observations can be high confidence regardless of model. Strong models are not automatically correct.

Preserve human-supplied scores. Low confidence may qualify both the reasoning AND whether the requested output is desirable: investigate and challenge tentative remedies before costly implementation. Do not silently assign humans scores. Separate confidence in the symptom, diagnosis and remedy where they differ.

Report: claim; confidence X/10; evidence/expertise; remaining uncertainty. For engineering evidence name the revision, reviewer model/provider, executed versus unrun checks, and what would raise confidence. A push is not verification. Carry this context into worker briefs and handoffs; re-evaluate when evidence or baseline changes. Correlated reviewers are not independent confirmation. Keep Todd's ownership and existing approval gates intact.
