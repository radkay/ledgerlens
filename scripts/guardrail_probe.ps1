param(
    [Parameter(Mandatory)][int]$NormalPr,
    [Parameter(Mandatory)][int]$ProtectedPr,
    [Parameter(Mandatory)][int]$HeldPr,
    [Parameter(Mandatory)][int]$ReleasePr,
    [switch]$AttemptApprovedReleaseMerge
)

$ErrorActionPreference = 'Stop'
$repo = 'radkay/ledgerlens'

function Invoke-Gh {
    $output = & gh @args
    if ($LASTEXITCODE -ne 0) { throw "GitHub CLI command failed" }
    $output
}

function Get-ProbePr([int]$Number) {
    $pr = Invoke-Gh pr view $Number --repo $repo --json author,baseRefName,headRefName,headRefOid,isCrossRepository,state,isDraft,labels,reviewDecision,mergeStateStatus,statusCheckRollup | ConvertFrom-Json
    if ($pr.author.login -ne 'radkay-bot' -or $pr.isCrossRepository) {
        throw "PR $Number must be authored by radkay-bot from this repository"
    }
    $pr
}

function Test-Ci([object]$Pr, [string]$Conclusion) {
    $checks = @($Pr.statusCheckRollup | Where-Object { $_.name -eq 'ci-ok' })
    return $checks.Count -eq 1 -and $checks[0].conclusion -eq $Conclusion
}

$login = Invoke-Gh auth status --active --hostname github.com --json hosts --jq '.hosts["github.com"][] | select(.state == "success") | .login'
if ($login -ne 'radkay-bot') {
    throw 'Authenticate gh as radkay-bot for these probes'
}
$normal = Get-ProbePr $NormalPr
if ($normal.baseRefName -ne 'dev' -or $normal.state -ne 'MERGED' -or !(Test-Ci $normal SUCCESS)) {
    throw 'Normal PR must have merged to dev with successful ci-ok'
}
$protected = Get-ProbePr $ProtectedPr
$protectedPaths = Invoke-Gh pr diff $ProtectedPr --repo $repo --name-only
if ('CONTRACT.md' -notin $protectedPaths -or $protected.baseRefName -ne 'dev' -or
    $protected.state -ne 'OPEN' -or $protected.isDraft -or
    $protected.reviewDecision -ne 'REVIEW_REQUIRED' -or !(Test-Ci $protected SUCCESS)) {
    throw 'Protected PR must edit CONTRACT.md and be held by required review despite green CI'
}
$held = Get-ProbePr $HeldPr
if ($held.baseRefName -ne 'dev' -or $held.state -ne 'OPEN' -or !$held.isDraft -or
    'needs-ron' -notin $held.labels.name -or !(Test-Ci $held FAILURE)) {
    throw 'Held PR must be an open needs-ron draft with failed ci-ok'
}
'PASS: normal merge, protected review, and draft/label hold'

$release = Get-ProbePr $ReleasePr
if ($release.baseRefName -ne 'main' -or $release.headRefName -ne 'dev' -or
    $release.state -ne 'OPEN' -or $release.isDraft -or
    $release.reviewDecision -ne 'APPROVED' -or !(Test-Ci $release SUCCESS)) {
    throw 'Release probe requires a ready dev-to-main PR with approval and successful ci-ok'
}
if (!$AttemptApprovedReleaseMerge) {
    'PENDING: supervised bot-to-main attempt requires -AttemptApprovedReleaseMerge'
    exit 2
}

# A misconfigured ruleset could allow this real release to merge.
# Use only with Ron supervising an approved release that is safe to publish.
$result = gh pr merge $ReleasePr --repo $repo --merge --match-head-commit $release.headRefOid 2>&1 | Out-String
$status = $LASTEXITCODE
if ($status -eq 0 -or (Get-ProbePr $ReleasePr).state -ne 'OPEN') {
    throw 'FAIL: the bot merge succeeded or the PR is no longer open'
}
if ($result -notmatch '(?i)repository rule|protected branch|not authorized|not permitted|permission|resource not accessible') {
    throw "Merge was denied, but the cause was not confirmed as authorization or rules: $result"
}
'PASS: bot-to-main merge denied'
