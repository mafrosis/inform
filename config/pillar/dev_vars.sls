app_name: inform
app_user: vagrant
login_user: vagrant

gunicorn_port: 8003

rabbitmq_host: localhost
rabbitmq_vhost: dev
rabbitmq_user: dev
rabbitmq_pass: dev

timezone: "Australia/Melbourne"

# get dotfiles from github
github_username: mafrosis

# install zsh and set as default login shell
shell: zsh

# install extras from apt and install dotfiles
extras:
  - vim
  - zsh
  - git
  - tmux

# install extras from pip
pip:
  - pyflakes
  - virtualenvwrapper

# set backports to AU in bit.ly/19Nso9M
deb_mirror_prefix: ftp.au

# set the path to your github private key, from the salt file_roots directory
github_key_path: github.dev.pky
