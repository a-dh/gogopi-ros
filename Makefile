help:
	@echo "targets: install remove"

LOC := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

services := hw_button_scan hw_wifi hw_find
service_files := $(addsuffix .service, $(services))

install: $(service_files)
	@for svc in $(service_files); do \
		install --mode=644 --target-directory /etc/systemd/system $$svc; \
		systemctl enable $$svc; \
		systemctl start $$svc; \
	done

remove:
	@for svc in $(services); do \
		systemctl stop $${svc}.service || true; \
		systemctl disable $${svc}.service || true; \
		rm -f /etc/systemd/system/$${svc}.service; \
	done
	systemctl daemon-reload

%.service: services/%.service.in
	sed -e 's@$${LOC}@'"$(LOC)@g" "$<" > "$@"

.PHONY: install remove
