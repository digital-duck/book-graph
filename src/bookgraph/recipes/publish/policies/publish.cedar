// publish.cedar: may this run force-push the built site to the branch? (recipes/publish; facts from bookgraph.publish.check)
// site_ok    the folder is a built site (index.html, domains/catalog.json)
// remote_ok  a GitHub repository URL or a local repository, and it answers `git ls-remote`
// branch_ok  not the remote's default branch, nor main/master: the push replaces the branch's whole history
// base_ok    built for the path it will be served at (/<repo>/ on project pages, / on <owner>.github.io or a custom domain)
permit (
    principal == Service::"book-graph",
    action == Action::"publish",
    resource
) when { context.site_ok && context.remote_ok && context.branch_ok && context.base_ok };
