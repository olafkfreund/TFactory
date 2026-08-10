{{/*
Expand the name of the chart.
*/}}
{{- define "tfactory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncated at 63 chars (DNS limit). Honours .Values.fullnameOverride.
*/}}
{{- define "tfactory.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name + version label.
*/}}
{{- define "tfactory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels — emitted on every resource.
*/}}
{{- define "tfactory.labels" -}}
helm.sh/chart: {{ include "tfactory.chart" . }}
{{ include "tfactory.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: tfactory
{{- end }}

{{/*
Selector labels — the SERVING identity. The Service selector and the Deployment's
own selector, and nothing a non-serving pod may carry.

NEVER put these on a Job or CronJob pod template. A Service selector is a SUBSET
match, so a Job pod carrying them joins the `tfactory` Service as an endpoint;
it listens on nothing, so kube-proxy hands it a share of real portal traffic and
answers with connection refused. This repo has already paid for that lesson once:
the portal-ui browser-test Job carried `app: tfactory`, Cloudflare served 502s for
the length of every test run, and the browser test took the portal it was testing
offline and then reported it broken (#885). Adding a `component` label does NOT
help — extra labels never exclude a pod from a selector.
*/}}
{{- define "tfactory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tfactory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name to use.
*/}}
{{- define "tfactory.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "tfactory.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image reference: respects .Values.image.repository + tag, defaulting
to the chart's appVersion when tag is empty.
*/}}
{{- define "tfactory.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}
