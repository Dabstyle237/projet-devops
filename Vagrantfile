Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"
  config.vm.hostname = "devops-vm"
  config.vm.network "public_network", bridge: "Intel(R) Wi-Fi 6 AX201 160MHz"
  config.vm.network "forwarded_port", guest: 5000, host: 5000
  config.vm.network "forwarded_port", guest: 9090, host: 9090
  config.vm.network "forwarded_port", guest: 3000, host: 3000
  config.vm.provider "virtualbox" do |vb|
    vb.memory = 3072
    vb.cpus = 2
  end
  config.vm.provision "shell", path: "provision.sh"
end