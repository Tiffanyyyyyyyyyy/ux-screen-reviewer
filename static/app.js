const loadSample = document.querySelector('#load-sample');
const form = document.querySelector('#review-form');
const reviewButton = document.querySelector('#review-button');
const reviewStatus = document.querySelector('#review-status');
const reviewError = document.querySelector('#review-error');
const liveReport = document.querySelector('#live-report');
const sample = document.querySelector('#sample');
const emptyReport = document.querySelector('#empty-report');
const downloadReport = document.querySelector('#download-report');
const nextActions = document.querySelector('#next-review-actions');
const addScreens = document.querySelector('#add-screens');
const newReview = document.querySelector('#new-review');
const refreshWorkspace = document.querySelector('#refresh-workspace');
const followUp = document.querySelector('#follow-up');
const answerFields = document.querySelector('#answer-fields');
const followUpStatus = document.querySelector('#follow-up-status');
let latestReview;
const screenshotInput = document.querySelector('#screenshots');
const previews = document.querySelector('#previews');
const uploadStatus = document.querySelector('#upload-status');
const screenshots = [];
const maxScreenshots = Number(screenshotInput.dataset.max);
const maxImageBytes = 8 * 1024 * 1024;
const connectionForm = document.querySelector('#connection-form');
const connectButton = document.querySelector('#connect-button');
const connectionStatus = document.querySelector('#connection-status');
const apiKey = document.querySelector('#api-key');
const connectedKey = document.querySelector('#connected-key');
let connected = !reviewButton.disabled;

document.querySelector('#replace-key').addEventListener('click', () => {
  connectedKey.hidden = true;
  connectionForm.hidden = false;
  connectionStatus.textContent = '';
  apiKey.required = true;
  apiKey.placeholder = 'Enter a new API key';
  apiKey.focus();
});

function diagnosticMessage(result, fallback) {
  const details = [];
  if (result.upstream_status) details.push(`OpenAI HTTP ${result.upstream_status}`);
  if (result.request_id) details.push(`request ID ${result.request_id}`);
  return [result.error || fallback, details.length ? `(${details.join(', ')})` : ''].filter(Boolean).join(' ');
}

function renderPreviews() {
  previews.replaceChildren();
  screenshots.forEach(({file, url}, index) => {
    const preview = document.createElement('div');
    preview.className = 'preview';
    const label = `Screen ${index + 1}`;
    const image = document.createElement('img');
    image.alt = `${label} preview`;
    image.src = url;
    image.addEventListener('error', () => {
      if (!preview.isConnected) return;
      image.hidden = true;
      appendText(preview, 'p', 'Preview unavailable. Remove this file and try another image.');
    });
    preview.append(image);
    appendText(preview, 'p', `${label} · ${file.name}`);
    const remove = appendText(preview, 'button', 'Remove');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remove ${label}`);
    remove.addEventListener('click', () => {
      URL.revokeObjectURL(url);
      screenshots.splice(index, 1);
      renderPreviews();
      uploadStatus.textContent = `${label} removed. ${screenshots.length} remaining.`;
      screenshotInput.focus();
    });
    previews.append(preview);
  });
}

screenshotInput.addEventListener('change', () => {
  const messages = new Set();
  for (const file of screenshotInput.files) {
    if (screenshots.length === maxScreenshots) {
      messages.add(`You can add up to ${maxScreenshots} screenshots.`);
      break;
    }
    if (!file.size || file.size > maxImageBytes) {
      messages.add('Each screenshot must be nonempty and 8 MiB or less.');
      continue;
    }
    screenshots.push({file, url: URL.createObjectURL(file)});
  }
  screenshotInput.value = '';
  renderPreviews();
  uploadStatus.textContent = messages.size ? [...messages].join(' ') : `${screenshots.length} screenshots added.${latestReview ? ' Select Review screen to review this set.' : ''}`;
});

connectionForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (connectButton.disabled) return;
  const body = JSON.stringify({api_key: apiKey.value.trim()});
  apiKey.value = '';
  connectButton.disabled = true;
  apiKey.disabled = true;
  reviewButton.disabled = true;
  connectionStatus.textContent = 'Finding the lowest-priced vision model available to your API project…';
  try {
    const response = await fetch(connectionForm.action, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body,
    });
    const result = await response.json();
    if (!response.ok) {
      connectionStatus.textContent = diagnosticMessage(result, 'Could not connect to OpenAI.');
      return;
    }
    connected = true;
    document.querySelector('#connection-state').textContent = result.model;
    connectionForm.hidden = true;
    connectedKey.hidden = false;
    document.querySelector('#replace-key').focus();
    reviewStatus.textContent = 'Screenshots and text are sent to OpenAI for review.';
  } catch {
    connectionStatus.textContent = 'Could not connect. Check the local server and try again.';
  } finally {
    connectButton.disabled = false;
    apiKey.disabled = false;
    reviewButton.disabled = !connected;
  }
});

loadSample.addEventListener('click', () => {
  if (!sample.hidden && latestReview) {
    sample.hidden = true;
    liveReport.hidden = false;
    followUp.hidden = !latestReview.report.open_questions.length;
    loadSample.textContent = 'Load sample';
    loadSample.setAttribute('aria-expanded', 'false');
    liveReport.querySelector('h3').focus();
    return;
  }
  liveReport.hidden = true;
  followUp.hidden = true;
  emptyReport.hidden = true;
  sample.hidden = false;
  downloadReport.hidden = false;
  loadSample.setAttribute('aria-expanded', 'true');
  loadSample.textContent = latestReview ? 'Show latest review' : 'Load sample';
  sample.querySelector('h3').focus();
});

downloadReport.addEventListener('click', () => {
  const isSample = !sample.hidden;
  const source = isSample ? sample : liveReport;
  const file = new Blob([source.innerText.trim() + '\n'], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = isSample ? 'ux-screen-review-sample.txt' : 'ux-screen-review.txt';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

addScreens.addEventListener('click', () => {
  screenshotInput.focus();
  if (screenshots.length < maxScreenshots) {
    screenshotInput.click();
  } else {
    uploadStatus.textContent = `You have ${maxScreenshots} screenshots. Remove one to add another, or start a new review.`;
  }
});

function resetReview() {
  screenshots.splice(0).forEach(({url}) => URL.revokeObjectURL(url));
  renderPreviews();
  latestReview?.imageUrls.forEach(url => URL.revokeObjectURL(url));
  latestReview = undefined;
  liveReport.replaceChildren();
  answerFields.replaceChildren();
  followUpStatus.textContent = '';
  liveReport.hidden = sample.hidden = followUp.hidden = nextActions.hidden = downloadReport.hidden = true;
  reviewError.hidden = true;
  emptyReport.hidden = false;
  loadSample.textContent = 'Load sample';
  loadSample.setAttribute('aria-expanded', 'false');
  uploadStatus.textContent = 'Workspace cleared. Add screenshots for a new review.';
  screenshotInput.focus();
}

newReview.addEventListener('click', resetReview);
refreshWorkspace.addEventListener('click', resetReview);

const coverageLabels = {
  layout: 'Layout and first impression',
  action: 'Action clarity',
  copy: 'Copy and intent',
  language: 'Language and fit',
  imagery: 'Imagery',
  colour: 'Colour',
};
const statusClasses = {
  Clear: 'clear',
  'Issue found': 'issue',
  'Needs verification': 'verify',
  'Not applicable': 'neutral',
};
const severityRank = {High: 0, Medium: 1, Low: 2};

function appendText(parent, tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.append(element);
  return element;
}

function reportSection(title) {
  const section = appendText(liveReport, 'section', '');
  appendText(section, 'h4', title);
  return section;
}

function reportDetails(parent, entries) {
  const details = appendText(parent, 'dl', '');
  details.className = 'report-details';
  for (const [label, value] of entries) {
    if (value === null) continue;
    appendText(details, 'dt', label);
    appendText(details, 'dd', value);
  }
}

function renderReadouts(section, screens, imageUrls) {
  screens.forEach((screen, index) => {
    const readout = appendText(section, 'div', '');
    readout.className = 'readout';
    const image = appendText(readout, 'img', '');
    image.src = imageUrls[index];
    image.alt = `${screen.screen}: ${screen.name}`;
    const summary = appendText(readout, 'div', '');
    appendText(summary, 'h5', screen.name);
    appendText(summary, 'p', [
      screens.length > 1 ? screen.screen : null, screen.locale, screen.audience,
    ].filter(Boolean).join(' · ')).className = 'report-meta';
    const action = screen.primary_action;
    reportDetails(summary, [
      ['Intended takeaway', screen.intended_takeaway ?? 'Unspecified'],
      ['Likely takeaway', screen.likely_takeaway],
      ['First impression', screen.first_impression],
      ['Primary action', action.label ? `“${action.label}” — ${action.findability}` : action.findability],
      ['Expected result', action.expected_result],
      ['Observed result', action.observed_result],
    ]);
  });
}

function renderFindings(section, report) {
  const targets = new Map();
  if (!report.findings.length) appendText(section, 'p', 'No fixes identified.');
  const screenLabels = new Map(report.screens.map(({screen, locale}) => [screen, `${screen} · ${locale}`]));
  const ordered = [...report.findings].sort((first, second) => severityRank[first.severity] - severityRank[second.severity]);
  ordered.forEach((finding, index) => {
    const card = appendText(section, 'article', '');
    card.className = 'finding';
    card.id = `review-finding-${index + 1}`;
    card.tabIndex = -1;
    targets.set(finding.id, {id: card.id, title: finding.title});
    const header = appendText(card, 'header', '');
    appendText(header, 'h5', finding.title);
    appendText(header, 'span', finding.severity).className = `report-badge ${finding.severity.toLowerCase()}`;
    if (report.screens.length > 1) {
      appendText(card, 'p', finding.screens.map(screen => screenLabels.get(screen)).join(' / ')).className = 'report-meta';
    }
    reportDetails(card, [
      ['Affected element', finding.element],
      ['Evidence', finding.evidence],
      ['User impact', finding.impact],
      ['Suggested fix', finding.fix],
      ['How to verify', finding.verification],
    ]);
  });
  return targets;
}

function renderCoverage(section, coverage, targets) {
  const rows = appendText(section, 'ul', '');
  rows.className = 'coverage-list';
  for (const [key, title] of Object.entries(coverageLabels)) {
    const {status, assessment, finding_ids, translation_fidelity} = coverage[key];
    const row = appendText(rows, 'li', '');
    appendText(row, 'strong', title);
    appendText(row, 'span', status).className = `report-badge ${statusClasses[status]}`;
    const line = appendText(row, 'p', assessment);
    for (const id of new Set(finding_ids)) {
      const target = targets.get(id);
      const link = appendText(line, 'a', 'View fix');
      link.href = `#${target.id}`;
      link.setAttribute('aria-label', `View fix: ${target.title}`);
    }
    if (translation_fidelity) {
      appendText(row, 'p', `Translation fidelity · ${translation_fidelity}`).className = 'report-meta';
    }
  }
}

function showReport(report, imageUrls) {
  liveReport.replaceChildren();
  const heading = appendText(liveReport, 'h3', 'Screen readout');
  heading.className = 'report-start';
  heading.tabIndex = -1;
  const readout = appendText(liveReport, 'section', '');
  readout.setAttribute('aria-label', 'Screen readout');
  renderReadouts(readout, report.screens, imageUrls);
  const targets = renderFindings(reportSection('Prioritized fixes'), report);
  renderCoverage(reportSection('Review coverage'), report.coverage, targets);
  if (report.open_questions.length) {
    const list = appendText(reportSection('Open questions'), 'ul', '');
    for (const {question} of report.open_questions) appendText(list, 'li', question);
  }
  answerFields.replaceChildren();
  followUpStatus.textContent = '';
  report.open_questions.forEach(({question}, index) => {
    const label = appendText(answerFields, 'label', question);
    const field = appendText(answerFields, 'textarea', '');
    field.id = `open-answer-${index}`;
    field.name = String(index);
    field.rows = 2;
    field.maxLength = 1000;
    label.htmlFor = field.id;
  });
  followUp.hidden = !report.open_questions.length;
  sample.hidden = true;
  emptyReport.hidden = true;
  loadSample.setAttribute('aria-expanded', 'false');
  loadSample.textContent = 'Load sample';
  liveReport.hidden = false;
  downloadReport.hidden = false;
  nextActions.hidden = false;
  heading.focus();
}

let reviewing = false;
form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (reviewButton.disabled || reviewing) return;

  if (!screenshots.length) {
    reviewError.textContent = 'Add at least one screenshot.';
    reviewError.hidden = false;
    screenshotInput.focus();
    return;
  }
  const data = new FormData(form);
  for (const {file} of screenshots) data.append('screenshots', file);
  submitReview(data);
});

followUp.addEventListener('submit', (event) => {
  event.preventDefault();
  if (reviewButton.disabled || reviewing || !latestReview) return;
  const answers = new Map(latestReview.answers.map(({question, answer}) => [question, answer]));
  const entries = [...new FormData(followUp)].filter(([, answer]) => answer.trim());
  if (!entries.length) {
    followUpStatus.textContent = 'Enter at least one answer.';
    return;
  }
  for (const [index, answer] of entries) {
    const question = latestReview.report.open_questions[Number(index)].question;
    answers.set(question, answer.trim());
  }
  submitReview(latestReview.data, [...answers].map(([question, answer]) => ({question, answer})));
});

async function submitReview(data, answers = []) {
  const isFollowUp = answers.length > 0;
  reviewError.hidden = true;
  followUpStatus.textContent = '';
  if (isFollowUp) data.set('answers', JSON.stringify(answers));
  else data.delete('answers');
  const fields = [
    ...form.querySelectorAll('input, textarea'), ...previews.querySelectorAll('button'),
    ...followUp.querySelectorAll('textarea, button'), addScreens, newReview, refreshWorkspace,
  ];
  const idleStatus = reviewStatus.textContent;
  const status = isFollowUp ? 'Re-evaluating report…' : 'Reviewing screenshots…';
  reviewing = true;
  reviewButton.disabled = true;
  loadSample.disabled = true;
  connectButton.disabled = true;
  fields.forEach(field => { field.disabled = true; });
  reviewStatus.textContent = status;
  if (isFollowUp) followUpStatus.textContent = status;

  function showError(message) {
    if (isFollowUp) {
      followUpStatus.textContent = message;
    } else {
      reviewError.textContent = message;
      reviewError.hidden = false;
    }
  }

  try {
    const response = await fetch(form.action, {method: 'POST', body: data});
    const result = await response.json();
    if (!response.ok) {
      showError(diagnosticMessage(result, 'The review failed. Try again.'));
      return;
    }
    const imageUrls = data.getAll('screenshots').map(file => URL.createObjectURL(file));
    latestReview?.imageUrls.forEach(url => URL.revokeObjectURL(url));
    latestReview = {report: result.report, data, answers, imageUrls};
    showReport(result.report, imageUrls);
  } catch {
    showError('The review could not be completed. Check the local server and try again.');
  } finally {
    reviewing = false;
    reviewButton.disabled = !connected;
    loadSample.disabled = false;
    connectButton.disabled = false;
    fields.forEach(field => { field.disabled = false; });
    reviewStatus.textContent = idleStatus;
  }
}
