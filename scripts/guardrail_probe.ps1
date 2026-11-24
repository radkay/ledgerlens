[CmdletBinding(DefaultParameterSetName = 'All')]
param(
    [Parameter(Mandatory, ParameterSetName = 'All')][int]$NormalPr,
    [Parameter(Mandatory, ParameterSetName = 'All')][int]$ProtectedPr,
    [Parameter(Mandatory, ParameterSetName = 'All')][int]$HeldPr,
    [Parameter(Mandatory, ParameterSetName = 'Release')][switch]$ReleaseOnly,
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
    $fields = 'author,baseRefName,headRefName,headRefOid,isCrossRepository,state,isDraft,labels,reviewDecision,reviewRequests,reviews,mergeStateStatus,statusCheckRollup'
    $pr = Invoke-Gh pr view $Number --repo $repo --json $fields | ConvertFrom-Json
    if ($pr.author.login -ne 'radkay-bot' -or $pr.isCrossRepository) {
        throw "PR $Number must be authored by radkay-bot from this repository"
    }
    $pr
}

function Test-Ci([object]$Pr, [string]$Conclusion) {
    $check = $Pr.statusCheckRollup |
        Where-Object { $_.name -eq 'ci-ok' -and $_.workflowName -eq 'PR CI' } |
        Sort-Object { [long]($_.detailsUrl.Split('/')[-1]) } -Descending |
        Select-Object -First 1
    return $null -ne $check -and $check.conclusion -eq $Conclusion
}

$login = Invoke-Gh auth status --active --hostname github.com --json hosts --jq '.hosts["github.com"][] | select(.state == "success") | .login'
if ($login -ne 'radkay-bot') {
    throw 'Authenticate gh as radkay-bot for these probes'
}
if (!$ReleaseOnly) {
    $normal = Get-ProbePr $NormalPr
    if ($normal.baseRefName -ne 'dev' -or $normal.state -ne 'MERGED' -or !(Test-Ci $normal SUCCESS)) {
        throw 'Normal PR must have merged to dev with successful ci-ok'
    }
    $protected = Get-ProbePr $ProtectedPr
    $protectedPaths = Invoke-Gh pr diff $ProtectedPr --repo $repo --name-only
    if ('CONTRACT.md' -notin $protectedPaths -or $protected.baseRefName -ne 'dev' -or
        $protected.state -ne 'OPEN' -or $protected.isDraft -or $protected.mergeStateStatus -ne 'BLOCKED' -or
        'radkay' -notin $protected.reviewRequests.login -or !(Test-Ci $protected SUCCESS)) {
        throw 'Protected PR must edit CONTRACT.md and be held by required review despite green CI'
    }
    $held = Get-ProbePr $HeldPr
    if ($held.baseRefName -ne 'dev' -or $held.state -ne 'OPEN' -or !$held.isDraft -or
        'needs-ron' -notin $held.labels.name -or !(Test-Ci $held FAILURE)) {
        throw 'Held PR must be an open needs-ron draft with failed ci-ok'
    }
    'PASS: normal merge, protected review, and draft/label hold'
}

$release = Get-ProbePr $ReleasePr
$ownerReview = $release.reviews |
    Where-Object {
        $_.author.login -eq 'radkay' -and $_.authorAssociation -eq 'OWNER' -and
        $_.state -in @('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED')
    } |
    Sort-Object submittedAt -Descending |
    Select-Object -First 1
if ($release.baseRefName -ne 'main' -or $release.headRefName -ne 'dev' -or
    $release.state -ne 'OPEN' -or $release.isDraft -or
    $ownerReview.state -ne 'APPROVED' -or $ownerReview.commit.oid -ne $release.headRefOid -or
    $release.reviewDecision -in @('REVIEW_REQUIRED', 'CHANGES_REQUESTED') -or
    !(Test-Ci $release SUCCESS)) {
    throw 'Release probe requires a ready dev-to-main PR with repository owner approval and successful ci-ok'
}
if (!$AttemptApprovedReleaseMerge) {
    'PENDING: supervised bot-to-main attempt requires -AttemptApprovedReleaseMerge'
    exit 2
}

# A misconfigured ruleset could allow this real release to merge.
# Use only with Ron supervising an approved release that is safe to publish.
# --admin skips gh's local merge-state check. GitHub still authorizes the
# merge request using the authenticated radkay-bot identity.
$result = gh pr merge $ReleasePr --repo $repo --admin --merge --match-head-commit $release.headRefOid 2>&1 | Out-String
$status = $LASTEXITCODE
if ($status -eq 0 -or (Get-ProbePr $ReleasePr).state -ne 'OPEN') {
    throw 'FAIL: the bot merge succeeded or the PR is no longer open'
}
if ($result -notmatch '(?i)repository rule|protected branch|base branch policy prohibits the merge|not authorized|not permitted|permission|resource not accessible') {
    throw "Merge was denied, but the cause was not confirmed as authorization or rules: $result"
}
'GitHub CLI result:'
$result.Trim()
'PASS: bot-to-main merge denied'
