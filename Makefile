PYTHON ?= python

.PHONY: film radio preview validate music doctor
film:
	$(PYTHON) -m pipeline film
radio:
	$(PYTHON) -m pipeline radio
preview:
	$(PYTHON) -m pipeline film --preview
validate:
	$(PYTHON) -m pipeline validate
music:
	$(PYTHON) -m pipeline music
doctor:
	$(PYTHON) -m pipeline doctor
