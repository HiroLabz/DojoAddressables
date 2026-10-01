# DojoAddressables

Stores pre-built Unity Addressables output, one top-level folder per environment.
Build content in `dojo-game-app`, then commit the bundles, catalogs, and hashes here.
This repository does not run Unity. Currently only `development/` is configured.

## Deployment

`.github/workflows/deploy-to-s3.yml` runs on pushes to `main`.

1. Detection selects changed, existing folders from `SUPPORTED_ENVIRONMENTS` in
   `.github/scripts/addressables.py`. README, workflow, editor, and unconfigured
   folder changes are ignored. Empty diffs and deleted environments are skipped.
   A new branch or unavailable pre-push commit selects all existing configured
   environments represented in the tracked files.
2. Each selected environment gets a job under the matching GitHub Environment.
   Jobs for the same environment cannot publish simultaneously.
3. After acquiring its deployment slot, the job checks out **the latest `main`**.
   Delayed runs and retries publish current content, not their original triggering
   commit. The deployed SHA is printed in the log. If the environment folder has
   since been removed, publication is skipped.
4. Validation checks configuration, nonempty assets, and matching catalog/hash
   filenames in the same directory. Flat and nested platform layouts work;
   symlinks are rejected. Validation does not decode catalogs, verify their hash
   contents, or check bundle references; test the build in Unity too.
5. GitHub OIDC supplies temporary AWS credentials. Bundles sync first, then all
   catalogs are copied, then all hashes are copied. Catalogs and hashes are always
   uploaded so their contents and cache headers are refreshed.

The destination is `s3://S3_BUCKET/S3_PREFIX/`; the environment folder name is
omitted. For example, `development/Android/catalog_0.1.0.bin` becomes
`s3://dojo-game-assets-dev/addressables/Android/catalog_0.1.0.bin`.

### Manual deployment

Open **Actions > Deploy Addressables to S3 > Run workflow**. Select branch `main`
and the desired environment. Dispatches from other branches are skipped. Use this
to retry publication after configuration changes or refresh catalog/hash metadata
without changing assets. Workflow-only changes do not automatically republish.

### GitHub configuration

Create **Settings > Environments > development** before the first deployment.
Under its **Environment variables**, configure:

| Variable | Value / scope |
| --- | --- |
| `S3_BUCKET` | Bucket name only, e.g. `dojo-game-assets-dev`; set per environment |
| `AWS_ROLE_ARN` | Deployment role ARN scoped to that bucket; set per environment |
| `AWS_REGION` | Bucket region; environment or repository variable |
| `S3_PREFIX` | Defaults to `addressables`; environment or repository variable |

Use Variables, not Secrets: the workflow reads `vars`. Repository variables live
under **Settings > Secrets and variables > Actions > Variables**. Prefixes must
have no leading/trailing slash, empty segments, `.`/`..` segments, or backslashes.
The workflow does not use AWS access-key secrets.

Restrict each environment's deployment branches to `main`. Development can deploy
without approval; configure required reviewers for production where supported by
your GitHub plan. Allow the checkout and AWS credential actions in repository or
organization Actions settings.

### AWS and Unity prerequisites

- Create one S3 bucket per environment. Keep ACLs disabled. A private bucket can
  serve downloads through CloudFront Origin Access Control (OAC).
- In **IAM > Identity providers**, configure GitHub OIDC using provider URL
  `https://token.actions.githubusercontent.com` and audience `sts.amazonaws.com`.
- Create a deployment role trusting this repository and the exact environment.
  For name-based OIDC subjects, development uses
  `repo:HiroLabz/DojoAddressables:environment:development`. If your repository uses
  immutable-ID or customized subjects, match its actual subject format.
- Grant the role `s3:ListBucket` on the bucket, restricted to the configured
  prefix, and `s3:PutObject` plus `s3:AbortMultipartUpload` on objects under that
  prefix. Customer-managed KMS encryption requires corresponding key permissions.
- Configure download access separately from upload access. With CloudFront OAC,
  grant the distribution read access through the bucket policy and keep S3 public
  access blocked. Set cache behavior to respect the catalog/hash 60-second TTL.
- In Unity, enable the remote catalog and set remote group/catalog load paths to
  the delivery URL, including the prefix and any platform subdirectory. Build for
  the intended platform and test remote loading after deployment.

References: [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[CloudFront OAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html).

### Caching and retention

- Bundles receive `public,max-age=31536000,immutable`. **Changed bundle content
  must receive a new filename**, using Unity's hash-based bundle naming. Sync does
  not enforce immutability or refresh cache headers on skipped bundles.
- Catalogs and hashes receive `public,max-age=60`. Publication is ordered but not
  atomic; clients/CDNs can temporarily observe different catalog/hash versions.
  There is no CloudFront invalidation. An atomic release protocol would require
  corresponding changes to client loading and the release layout.
- Nothing is deleted from S3. Old bundles remain available to existing clients.
  Plan retention around supported client versions; blanket expiry can break them.
- To roll back content, commit the desired catalog/hash and required bundles to
  `main`, then deploy. Re-running an old workflow still publishes latest `main`.

### Adding an environment

1. Provision its bucket, scoped IAM role, and download URL.
2. Create the matching GitHub Environment, variables, and protection rules
   **before pushing its assets**.
3. Add its name to `SUPPORTED_ENVIRONMENTS` in `.github/scripts/addressables.py`
   and to the workflow's manual input options.
4. Commit built output under that root folder and push to `main`.

## Local checks

Run `python -m unittest discover -s .github/scripts -v` for detection and validation
regression tests. They use temporary Git repositories and do not contact AWS.
