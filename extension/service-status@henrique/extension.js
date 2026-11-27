// Service Status: bolinha no painel com o pior status entre os serviços monitorados.
// A extensão apenas LÊ o status.json gerado pelo coletor Python; não faz checks de rede.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

// Permite usar await em load_contents_async
Gio._promisify(Gio.File.prototype, 'load_contents_async');

const INTERVALO_LEITURA_S = 5;
const SERVICO_SYSTEMD = 'service-status.service';
const CAMINHO_STATUS = GLib.build_filenamev(
    [GLib.get_user_cache_dir(), 'service-status', 'status.json']);
const ROTULOS = {ok: 'Normal', degraded: 'Degradado', down: 'Fora do ar', unknown: 'Indeterminado'};

// Qualquer valor fora do esperado é tratado como unknown (cinza)
const validar = status => (status in ROTULOS ? status : 'unknown');

function criarBolinha(status) {
    return new St.Widget({
        style_class: `ss-dot ss-${validar(status)}`,
        y_align: Clutter.ActorAlign.CENTER,
    });
}

const Indicador = GObject.registerClass(
class Indicador extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Service Status');

        this._bolinha = criarBolinha('unknown');
        this.add_child(this._bolinha);
        this._ultimoConteudo = null;
        this._destruido = false;

        this._renderizar({error: 'Aguardando o coletor...'}, false);
        this._atualizar();
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, INTERVALO_LEITURA_S, () => {
            this._atualizar();
            return GLib.SOURCE_CONTINUE;
        });

        this.connect('destroy', () => {
            this._destruido = true;
            if (this._timeoutId) {
                GLib.source_remove(this._timeoutId);
                this._timeoutId = 0;
            }
        });
    }

    async _atualizar() {
        let dados;
        try {
            const arquivo = Gio.File.new_for_path(CAMINHO_STATUS);
            const [conteudo] = await arquivo.load_contents_async(null);
            dados = JSON.parse(new TextDecoder().decode(conteudo));
        } catch (e) {
            const naoExiste = e instanceof GLib.Error &&
                e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND);
            dados = {error: naoExiste
                ? 'Coletor não encontrado (status.json ausente)'
                : `Erro ao ler o status: ${e.message}`};
        }
        if (this._destruido)
            return;

        // Dados velhos indicam que o timer/coletor parou de rodar
        const geradoEm = Date.parse(dados.generated_at);
        const limiteMs = (dados.interval_seconds ?? 60) * 3 * 1000;
        const desatualizado = !dados.error && (Number.isNaN(geradoEm) || Date.now() - geradoEm > limiteMs);

        this._renderizar(dados, desatualizado);
    }

    _renderizar(dados, desatualizado) {
        // Só reconstrói o menu quando algo mudou, para não piscar com o menu aberto
        const chave = JSON.stringify(dados) + desatualizado;
        if (chave === this._ultimoConteudo)
            return;
        this._ultimoConteudo = chave;

        let geral = 'unknown';
        let cabecalho;
        if (dados.error) {
            cabecalho = dados.error;
        } else if (desatualizado) {
            cabecalho = 'Dados desatualizados: o coletor parou?';
        } else {
            geral = validar(dados.overall);
            cabecalho = `Geral: ${ROTULOS[geral]}`;
        }
        this._bolinha.style_class = `ss-dot ss-${geral}`;

        this.menu.removeAll();
        this.menu.addMenuItem(new PopupMenu.PopupMenuItem(cabecalho, {reactive: false}));
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        for (const servico of dados.services ?? [])
            this.menu.addMenuItem(this._itemServico(servico));

        if (dados.generated_at && !Number.isNaN(Date.parse(dados.generated_at))) {
            const hora = new Date(Date.parse(dados.generated_at)).toLocaleTimeString();
            this.menu.addMenuItem(new PopupMenu.PopupMenuItem(`Atualizado às ${hora}`, {reactive: false}));
        }

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const verificar = new PopupMenu.PopupMenuItem('Verificar agora');
        verificar.connect('activate', () => this._verificarAgora());
        this.menu.addMenuItem(verificar);
    }

    _itemServico(servico) {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false});
        item.add_child(criarBolinha(servico.status));
        item.add_child(new St.Label({
            text: String(servico.name ?? servico.id),
            style_class: 'ss-nome',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        item.add_child(new St.Label({
            text: String(servico.detail ?? ''),
            style_class: 'ss-detalhe',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        return item;
    }

    // Executa o coletor imediatamente via systemd e relê o status.json quando ele termina
    _verificarAgora() {
        try {
            const proc = Gio.Subprocess.new(
                ['systemctl', '--user', 'start', SERVICO_SYSTEMD], Gio.SubprocessFlags.NONE);
            proc.wait_check_async(null, (p, resultado) => {
                try {
                    p.wait_check_finish(resultado);
                } catch (e) {
                    console.error(`[service-status] falha ao rodar o coletor: ${e.message}`);
                }
                if (!this._destruido)
                    this._atualizar();
            });
        } catch (e) {
            console.error(`[service-status] systemctl indisponível: ${e.message}`);
        }
    }
});

export default class ServiceStatusExtension extends Extension {
    enable() {
        this._indicador = new Indicador();
        Main.panel.addToStatusArea(this.uuid, this._indicador);
    }

    disable() {
        this._indicador?.destroy();
        this._indicador = null;
    }
}
