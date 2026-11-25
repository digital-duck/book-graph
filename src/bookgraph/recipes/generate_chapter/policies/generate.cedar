// generate.cedar: may this run write the chapter's pages and cache into output_dir / cache_dir?
// write_ok: both are absolute and under the user's home or the temporary folder (bookgraph.steps.write_root_ok;
// more roots in BOOKGRAPH_WRITE_ROOTS).
permit (
    principal == Service::"book-graph",
    action == Action::"generate_chapter",
    resource
) when { context.write_ok };
