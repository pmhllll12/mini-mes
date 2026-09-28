{{/* Release 이름을 그대로 리소스 접두어로 사용한다 (단일 인스턴스 로컬 데모용, nameOverride 등은 두지 않음). */}}
{{- define "mini-mes.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "mini-mes.labels" -}}
app.kubernetes.io/name: mini-mes
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "mini-mes.selectorLabels" -}}
app.kubernetes.io/name: mini-mes
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
