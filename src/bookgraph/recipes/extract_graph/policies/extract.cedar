// extract.cedar: may this run write the chapter's graph.yaml and concept_sources.yaml into out_dir?
// write_ok: out_dir is absolute and under the user's home, the temporary folder, or a root passed as write_roots
// (bookgraph.steps.write_root_ok; the caller reads BOOKGRAPH_WRITE_ROOTS).
permit (
    principal == Service::"book-graph",
    action == Action::"extract_graph",
    resource
) when { context.write_ok };
