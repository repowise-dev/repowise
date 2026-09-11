{{- define "repowise.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "repowise.fullname" -}}
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

{{- define "repowise.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/name: {{ include "repowise.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "repowise.selectorLabels" -}}
app.kubernetes.io/name: {{ include "repowise.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "repowise.secretName" -}}
{{- if .Values.secret.existingSecret }}
{{- .Values.secret.existingSecret }}
{{- else }}
{{- include "repowise.fullname" . }}-secret
{{- end }}
{{- end }}

{{- define "repowise.dbUrl" -}}
{{- if .Values.postgresql.enabled }}
{{- $host := default (printf "%s-postgresql" .Release.Name) .Values.postgresql.host }}
{{- $passwordKey := .Values.postgresql.secretKey }}
{{- printf "postgresql+asyncpg://%s:$(REPOWISE_DB_PASSWORD)@%s:%d/%s" .Values.postgresql.user $host (int .Values.postgresql.port) .Values.postgresql.database }}
{{- else }}
{{- printf "sqlite+aiosqlite:////data/wiki.db" }}
{{- end }}
{{- end }}

{{- define "repowise.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "repowise.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
