# WikiWare
gurt

^ my beloved gurt will not be removed.

# Table of contents
- [Installation](#installation)

## Installation
Install Wikiware by git cloning the project, via

`git clone https://github.com/superintendent2521/wikiware.git`

Then, after it is completed,

`cd wikiware`

Install your requirements
`pip install -r requirements.txt`

It requires `bleach` for HTML sanitization. While this library is not actively maintained, it remains a necessary dependency for security until a suitable modern alternative is implemented.

PostgreSQL is now the primary data store. (priorly postgres) Provide a `DATABASE_URL` in your `.env` (for example `postgresql://wikiware:wikiware@localhost:5432/wikiware`) before starting the server.

IT REQUIRES S3 FOR IMAGES!
