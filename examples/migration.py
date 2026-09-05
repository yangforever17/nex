# Model-authored input: run through WorkflowCompiler and Runtime, not Python directly.


def migrate(sites):
    observations = observe(sites[:2])
    rule = semantic(observations, "Migrate timeouts to seconds")
    for site in sites:
        apply_change(site, rule)
    publish_report(sites)
    return final_validate()
