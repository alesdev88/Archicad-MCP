# Privacy Policy

**Effective date:** 1 September 2026
**Applies to:** the `archicad-mcp` MCP server and its Claude Desktop extension,
in every version distributed from
[this repository](https://github.com/alesdev88/Archicad-MCP).

## Summary

This server runs entirely on your own computer and talks to one thing: the
Archicad instance already running beside it. It has no backend. The author
receives no data from it of any kind, including error reports and usage counts.

## What the server collects

Nothing is collected in the sense of being gathered and sent somewhere. The
server reads what you ask it to read, hands the answer back to the AI client
that asked, and keeps none of it.

To answer a request it may read, from the Archicad project you have open:

- element identifiers, types, layers, stories, classifications and geometry
- property definitions and property values
- attribute names: layers, building materials, composites, surfaces, profiles
- project metadata: project name, file location, story structure, hotlinks,
  Teamwork state, and whether a geolocation is set
- the current selection, and the project's issue list
- schedule scheme XML files, and rule files, that you point it at by path

## Where that data goes

To the MCP client that called the tool, and nowhere else. In the normal setup
that client is Claude Desktop, which sends the tool's result to Anthropic as
part of your conversation, exactly as it does with anything else in that
conversation. **Anthropic's handling of it is governed by
[Anthropic's Privacy Policy](https://www.anthropic.com/legal/privacy), not by
this one.** If you connect this server to a different MCP client, that client's
policy applies instead.

The server itself makes no outbound network connections. It opens TCP
connections to `127.0.0.1` on ports 19723 to 19743, which is the Archicad JSON
API listening on your own machine, and to nothing else. There is no telemetry,
no analytics, no crash reporting, and no license or update check.

## Two deliberate reductions

**`verdicts` mode.** Setting the mode to `verdicts` exposes only the eight QA
tools and blanks the project name out of `list_instances`, so a compliance check
can run without the project's identity entering the conversation at all.

**Teamwork credential stripping.** On a Teamwork project, Archicad reports the
project location as a `teamwork://user:<token>@host/path` URL in which the token
is a live credential. The server removes the credential segment before returning
the location, and applies a regex backstop that redacts any JWT-shaped string
surviving elsewhere in the response, so a working credential is not placed into
a model context or a session transcript.

## What is stored, and for how long

The server keeps no database, no cache and no history. It holds data only in
memory, for the duration of the request that asked for it.

Three things do touch your disk, all of them only because you asked for them by
name, all of them written where you specify, and none of them read back by the
server afterwards:

- BCF files written by `export_issues_bcf`
- edited schedule scheme XML written by `edit_schedule_scheme`
- output produced by `publish`, written by Archicad's own publisher

The server also writes a short startup line to standard error, naming the mode,
the rule count, and the Archicad instances it found, including their project
names. Claude Desktop captures that into its own log file on your machine
(`%APPDATA%\Claude\logs\` on Windows, `~/Library/Logs/Claude/` on macOS). Those
logs are yours, retained under Claude Desktop's rules, and are not transmitted
to the author.

Your office rule files stay wherever you put them. The server reads them; it
never writes to them.

## Sharing with third parties

None. There is no third party to share with: no hosting provider, no analytics
vendor, no error tracker, no advertising network. Nothing is sold, and nothing
is disclosed, because nothing is received.

The Archicad JSON API and the optional
[Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation) run on
your machine as part of your Archicad installation. Communication with them does
not leave the machine.

## Children

This is professional BIM software. It is not directed at children and collects
nothing from anyone.

## Changes to this policy

Material changes will be published in this file and noted in the release notes
of the version that carries them. The effective date above changes with them.

## Contact

Questions, or a privacy problem to report:
[github.com/alesdev88/Archicad-MCP/issues](https://github.com/alesdev88/Archicad-MCP/issues).
