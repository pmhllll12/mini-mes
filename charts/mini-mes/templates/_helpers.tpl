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

{{/* 정밀가공 데이터 적재 파드 spec (machining-data.yaml의 훅 Job·CronJob 공용). 인자: root, configMap, args */}}
{{- define "mini-mes.machiningLoadPod" -}}
restartPolicy: Never
initContainers:
  - name: wait-for-db
    image: {{ .root.Values.db.image }}
    command:
      - sh
      - -c
      - |
        until pg_isready -h {{ include "mini-mes.fullname" .root }}-db -p {{ .root.Values.db.port }} -U {{ .root.Values.db.user }} -d {{ .root.Values.db.database }}; do
          echo "DB 대기 중..."; sleep 2
        done
containers:
  - name: load
    image: "{{ .root.Values.image.repository }}:{{ .root.Values.image.tag }}"
    imagePullPolicy: {{ .root.Values.image.pullPolicy }}
    command:
      - sh
      - -c
      - |
        if [ ! -f /data/{{ .root.Values.machiningData.fileName }} ]; then
          echo "ConfigMap {{ .configMap }}에 {{ .root.Values.machiningData.fileName }}이 없어 적재를 건너뜀"; exit 0
        fi
        exec python load_machining_quality.py --csv /data/{{ .root.Values.machiningData.fileName }} {{ .args }}
    env:
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: {{ include "mini-mes.fullname" .root }}-db
            key: DATABASE_URL
    volumeMounts:
      - name: data
        mountPath: /data
        readOnly: true
volumes:
  - name: data
    configMap:
      name: {{ .configMap }}
      optional: true
{{- end -}}
