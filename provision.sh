#!/bin/bash
apt-get update -y
curl -fsSL https://get.docker.com | sh
usermod -aG docker vagrant
apt-get install -y docker-compose-plugin git