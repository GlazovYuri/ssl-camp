"""Верхнеуровневый код стратегии"""
# pylint: disable=redefined-outer-name

# @package Strategy
# Расчет требуемых положений роботов исходя из ситуации на поле

import math

# !v DEBUG ONLY
import typing
from enum import Enum
from time import time
from typing import Optional

import bridge.processors.auxiliary as aux
import bridge.processors.const as const
import bridge.processors.drawing as draw
import bridge.processors.field as fld
import bridge.processors.ref_states as refs
import bridge.processors.robot as robot
import bridge.processors.signal as signal
import bridge.processors.twisted_kick as twist
import bridge.processors.waypoint as wp


class States(Enum):
    """Класс с глобальными состояниями игры"""

    DEBUG = 0
    DEFENSE = 1
    ATTACK = 2


class GameStates(Enum):
    """Класс с командами от судей"""

    HALT = 0
    STOP = 1
    RUN = 2
    TIMEOUT = 3
    PREPARE_KICKOFF = 5
    KICKOFF = 6
    PREPARE_PENALTY = 7
    PENALTY = 8
    FREE_KICK = 9
    BALL_PLACEMENT = 11


class ActiveTeam(Enum):
    """Класс с командами"""

    ALL = 0
    YELLOW = 1
    BLUE = 2

class Role(Enum):
    """Класс с ролями"""

    GOALKEEPER = 0
    WALLLINER = 1
    # PRESSING = 2

    ATTACKER = 10
    POPUSK = 11

    PREV_ROLES: list["Role"] = []

class Strategy:
    """Основной класс с кодом стратегии"""

    def __init__(self, dbg_game_status: GameStates = GameStates.RUN, dbg_state: States = States.ATTACK) -> None:
        self.refs = refs.RefStates()

        self.game_status = dbg_game_status
        self.active_team: ActiveTeam = ActiveTeam.ALL
        self.state = dbg_state
        self.timer = time()
        self.timer1 = None
        self.is_ball_moved = 0

        # DEFENSE
        self.old_def_helper = -1
        self.old_def = -1
        self.steal_flag = 0

        # ATTACK
        self.robot_with_ball: typing.Optional[int] = None
        self.connector: list[int] = []
        # self.popusk: list[int] = []
        self.attack_state = "TO_BALL"
        self.attack_pos = aux.Point(0, 0)
        self.calc = False
        self.point_res = aux.Point(0, 0)
        self.used_pop_pos = [False, False, False, False, False]

        self.image = draw.Image()
        self.image.draw_field()
        self.ball_start_point: Optional[aux.Point] = None
        self.twist_w: float = 0.5

        self.wait_kick_timer: Optional[float] = None

        self.desision_flag = 0
        self.current_dessision = [None, 0]
        self.desision_border = 0.2

        self.ball_history:list[Optional[aux.Point]] = [None] * round(0.3 / const.Ts)
        self.ball_pass_update_timer = 0
        self.ball_history_idx = 0
        self.old_ball_pos = None

    def change_game_state(self, new_state: GameStates, upd_active_team: int) -> None:
        """Изменение состояния игры и цвета команды"""
        self.game_status = new_state
        if upd_active_team == 0:
            self.active_team = ActiveTeam.ALL
        elif upd_active_team == 2:
            self.active_team = ActiveTeam.YELLOW
        elif upd_active_team == 1:
            self.active_team = ActiveTeam.BLUE

    def choose_roles(self, field: fld.Field) -> tuple[list[Role], list[Role]]:
        """
        Определение ролей для роботов на поле
        """

        ATTACK_ROLES = [
            Role.ATTACKER,
            Role.POPUSK,
            Role.POPUSK,
            Role.POPUSK,
            Role.POPUSK,
            Role.POPUSK,
        ]
        DEFENSE_ROLES = [
            Role.GOALKEEPER,
            # Role.PRESSING,
            Role.WALLLINER,
            Role.WALLLINER,
            Role.WALLLINER,
            Role.WALLLINER,
            Role.WALLLINER,
        ]

        atk_min = 1
        def_min = 1

        free_allies = len(field.allies) - atk_min - def_min
        defs = round(free_allies / (2 * const.GOAL_DX) * (field.ball.get_pos().x * field.polarity + const.GOAL_DX)) + def_min
        atks = free_allies - (defs - def_min) + atk_min

        # print(ATTACK_ROLES[:atks], DEFENSE_ROLES[:defs])
        return ATTACK_ROLES[:atks], DEFENSE_ROLES[:defs]

    def process(self, field: fld.Field) -> list[wp.Waypoint]:
        """
        Рассчитать конечные точки для каждого робота
        """
        if self.active_team == ActiveTeam.ALL:
            self.refs.we_active = 1
        elif field.ally_color == "b":
            if self.active_team == ActiveTeam.BLUE:
                self.refs.we_active = 1
            else:
                self.refs.we_active = 0
        else:
            if self.active_team == ActiveTeam.YELLOW:
                self.refs.we_active = 1
            else:
                self.refs.we_active = 0

        if self.ball_history[self.ball_history_idx] is None:
            self.ball_start_point = self.ball_history[0]
        else:
            self.ball_start_point = self.ball_history[self.ball_history_idx]

        self.ball_history[self.ball_history_idx] = field.ball.get_pos()
        self.ball_history_idx += 1
        self.ball_history_idx %= len(self.ball_history)

        waypoints: list[wp.Waypoint] = []
        for i in range(const.TEAM_ROBOTS_MAX_COUNT):
            waypoints.append(wp.Waypoint(field.allies[i].get_pos(), field.allies[i].get_angle(), wp.WType.S_ENDPOINT))

        if self.game_status != GameStates.PENALTY:
            self.refs.is_started = 0

        if self.game_status == GameStates.RUN:
            self.run(field, waypoints)
        else:
            if self.game_status == GameStates.TIMEOUT:
                self.refs.timeout(field, waypoints)
            elif self.game_status == GameStates.HALT:
                pass
                # self.halt(field, waypoints)
            elif self.game_status == GameStates.PREPARE_PENALTY:
                self.refs.prepare_penalty(field, waypoints)
            elif self.game_status == GameStates.PENALTY:
                if self.refs.we_kick or 1: ###########################
                    self.refs.penalty_kick(field, waypoints)
                else:
                    robot_with_ball = fld.find_nearest_robot(field.ball.get_pos(), field.enemies)
                    self.goalk(field, waypoints, [const.GK], robot_with_ball)
            elif self.game_status == GameStates.BALL_PLACEMENT:
                self.refs.keep_distance(field, waypoints)
            elif self.game_status == GameStates.PREPARE_KICKOFF:
                self.refs.prepare_kickoff(field, waypoints)
            elif self.game_status == GameStates.KICKOFF:
                self.refs.kickoff(field, waypoints)
                robot_with_ball = fld.find_nearest_robot(field.ball.get_pos(), field.enemies)
                self.goalk(field, waypoints, [const.GK], robot_with_ball)
            # elif self.game_status == GameStates.FREE_KICK:
            #     self.free_kick(field, waypoints)
            elif self.game_status == GameStates.STOP:
                self.refs.keep_distance(field, waypoints)
        # print(self.game_status, self.state)

        for i in [14, 11]:
            self.image.draw_robot(field.allies[i].get_pos(), field.allies[i].get_angle())
        self.image.draw_dot(field.ball.get_pos(), 5)
        self.image.draw_poly(field.ally_goal.hull)
        self.image.draw_poly(field.enemy_goal.hull)
        self.image.update_window()
        self.image.draw_field()

        return waypoints

    def run(self, field: fld.Field, waypoints: list[wp.Waypoint]) -> None:
        """Определение глобального состояния игры"""
        for i in range(const.TEAM_ROBOTS_MAX_COUNT):
            waypoints[i] = wp.Waypoint(field.allies[i].get_pos(), field.allies[i].get_angle(), wp.WType.S_STOP)

        # pnt = self.choose_kick_point(field, a_id)
        # if pnt is None:
        #     pnt = field.enemy_goal.center

        # waypoints[a_id] = wp.Waypoint(field.ball.get_pos(), aux.angle_to_point(field.ball.get_pos(), pnt), wp.WType.S_BALL_KICK)
        # self.goalk(field, waypoints, [const.GK], None)

        #pass_pnt = self.estimate_pass_point(field, field.allies[self.k].get_pos(), field.allies[self.r].get_pos())

        #goal2_pnt = self.choose_kick_point(field, self.r, field.allies[self.r].get_pos())[1]

        # self.pass_receiver(field, waypoints, self.r, self.r_pos)
        atk_roles, def_roles = self.choose_roles(field)

        if len(atk_roles) > 0:
            attacker_id = fld.find_nearest_robot(field.ball.get_pos(), field.allies, [const.GK]).r_id
            print("attacker_id: ", attacker_id)
            self.popusks = field.find_popusks(len(atk_roles) - 1, attacker_id)
            waypoints[attacker_id] = self.attacker(field, attacker_id)

            self.set_popusk_wps(field, waypoints, field.ball.get_pos())
            


        # self.pass_receiver(field, waypoints, self.r, self.r_pos)
        self.goalk(field, waypoints, [const.GK], field.ally_with_ball)
        # self.goalk(field, waypoints, [13], None)

        '''
        if not field.is_ball_moves_to_point(field.allies[self.r].get_pos()):
            kicker = fld.find_nearest_robot(field.ball.get_pos(), field.allies)
            old = self.desision_flag
            if kicker.r_id == 11:
                self.desision_flag = 0
                self.k = 11
                self.r = 14
                self.r_pos = aux.Point(-2500, -1500)
            else:
                self.desision_flag = 1
                self.k = 14
                self.r = 11
                self.r_pos = aux.Point(-2500, 1500)
            if old != self.desision_flag:
                self.desision_border = 0.3

        self.pass_receiver(field, waypoints, self.r, self.r_pos)

        # GRAB AND KICK
        id = self.k
        if field.is_ball_in(field.allies[id]):
            # kick_point = field.allies[14].get_pos()
            self.tmp = field.allies[self.r].get_pos()
            self.kick_with_rotation(field, waypoints, id, self.tmp)
            print("in")
        else:
            waypoints[id] = wp.Waypoint(field.ball.get_pos(), aux.angle_to_point(field.allies[id].get_pos(), field.ball.get_pos()), wp.WType.S_BALL_GRAB)
            self.wait_kick = False
            self.start_rotating_ang = None
            print("not in")
            self.twist_w = 0.5
        '''



    square = signal.Signal(15, "SQUARE", lohi=(-2000, -1000))
    square_ang = signal.Signal(4, "SQUARE", lohi=(0, 4))

    def debug(self, field: fld.Field, waypoints: list[wp.Waypoint]) -> list[wp.Waypoint]:
        """Отладка"""

        robot_with_ball = fld.find_nearest_robot(field.ball.get_pos(), field.allies)
        self.goalk(field, waypoints, [const.GK], robot_with_ball)

        waypoints[const.DEBUG_ID].pos = field.ball.get_pos()
        waypoints[const.DEBUG_ID].angle = (field.ally_goal.center - field.ball.get_pos() + aux.UP * self.square.get()).arg()
        waypoints[const.DEBUG_ID].type = wp.WType.S_BALL_KICK

        # print(field.ball.get_pos(), waypoints[const.DEBUG_ID])
        return waypoints

    def attacker(self, field: fld.Field, attacker_id: int) -> wp.Waypoint:
        tmp = self.choose_kick_point(field, attacker_id)

        est = self.estimate_pass_point(field, field.ball.get_pos(), tmp[0])
        # print(max(est, self.estimate_pass_point(field, field.ball.get_pos(), self.current_dessision[0])))
        if est >= self.estimate_pass_point(field, field.ball.get_pos(), self.current_dessision[0]):
            self.current_dessision[0] = tmp[0]
            self.current_dessision[1] = est

        if self.current_dessision[1] < self.desision_border and len(self.popusks) > 0:
            receiver_id = self.choose_receiver(field)
            waypoint = self.pass_kicker(field, receiver_id)
        else:
            waypoint =  wp.Waypoint(
                field.ball.get_pos(), aux.angle_to_point(field.allies[attacker_id].get_pos(), self.current_dessision[0]), wp.WType.S_BALL_KICK
            )

        return waypoint

    def choose_receiver(self, field: fld.Field) -> int:
        receiver_id = None
        receiver_score = 0.0
        for popusk in self.popusks:
            pass_score = self.estimate_pass_point(field, field.ball.get_pos(), self.current_dessision[0])
            kick_score = self.choose_kick_point(field, popusk.r_id)[1]
            score = pass_score * kick_score
            if score < receiver_score or receiver_id is None:
                receiver_id = popusk.r_id
                receiver_score = score
        return receiver_id

    def set_popusk_wps(self, field: fld.Field, waypoints: list[wp.Waypoint], ball_pos: aux.Point) -> None:
        pos_num = len(self.popusks)
        poses = [aux.Point(3500 * field.polarity, 1500), aux.Point(3500 * field.polarity, -1500), aux.Point(2500 * field.polarity, 0)]
        poses = poses[:(pos_num + 1)]
        bad_pos = aux.find_nearest_point(ball_pos, poses)

        used_popusks: list[int] = []

        for pos in poses:
            if pos == bad_pos:
                continue
            if len(used_popusks) == len(self.popusks):
                return
            pop = fld.find_nearest_robot(pos, self.popusks, used_popusks)
            used_popusks.append(pop.r_id)
            self.pass_receiver(field, waypoints, pop.r_id, pos)
            print(pop.r_id, pos)

    def pass_kicker(self, field: fld.Field, receiver_id: int) -> Optional[wp.Waypoint]:
        """
        Отдает пас от робота kicker_id роботу receiver_id
        Должна вызываться в конечном автомате постоянно, пока первый робот не даст пас
        """
        receiver = field.allies[receiver_id]
        if not field.is_ball_moves_to_point(receiver.get_pos()):
            waypoint = wp.Waypoint(
                field.ball.get_pos(),
                aux.angle_to_point(field.ball.get_pos(), receiver.get_pos()),
                wp.WType.S_BALL_PASS,
            )
            self.image.draw_dot(
                field.ball.get_pos() + aux.rotate(aux.RIGHT, aux.angle_to_point(field.ball.get_pos(), receiver.get_pos())),
                5,
                (255, 0, 255),
            )
        else:
            waypoint = wp.Waypoint(aux.Point(0,0), 0, wp.WType.S_STOP)
        return waypoint

    def pass_receiver(
        self, field: fld.Field, waypoints: list[wp.Waypoint], receiver_id: int, receive_point: aux.Point
    ) -> None:
        """
        Отдает пас от робота kicker_id роботу receiver_id
        Должна вызываться в конечном автомате постоянно, пока второй робот не поймает мяч
        TODO: прописать действия отдающего пас робота после удара и принимающего пас робота до удара
        """
        receiver = field.allies[receiver_id]
        if (
            field.is_ball_moves_to_point(receiver.get_pos())
            and self.ball_start_point is not None
            and (self.ball_start_point - field.ball.get_pos()).mag() > const.INTERCEPT_SPEED
            and field.ally_with_ball is None
        ):
            target = aux.closest_point_on_line(self.ball_start_point, field.ball.get_pos(), receiver.get_pos(), "R")
            self.image.draw_dot(target, 5, (255, 255, 0))

            waypoints[receiver_id] = wp.Waypoint(
                target, aux.angle_to_point(field.ball.get_pos(), self.ball_start_point), wp.WType.S_ENDPOINT
            )
        else:
            waypoints[receiver_id] = wp.Waypoint(
                receive_point, aux.angle_to_point(receiver.get_pos(), field.ball.get_pos()), wp.WType.S_ENDPOINT
            )
            self.image.draw_dot(receive_point, 5, (255, 255, 0))

    def kick_with_rotation(
        self, field: fld.Field, waypoints: list[wp.Waypoint], kicker_id: int, kick_point: aux.Point
    ) -> None:
        """
        Прицеливание и удар в точку при условии, что мяч находится в захвате у робота
        """
        kicker = field.allies[kicker_id]

        # signed_A = aux.wind_down_angle(aux.angle_to_point(kicker.get_pos(), kick_point) - self.start_rotating_ang)
        # A = abs(signed_A)
        signed_x = aux.wind_down_angle(aux.angle_to_point(kicker.get_pos(), kick_point) - kicker.get_angle())
        x = abs(signed_x)

        beta = 3
        a = beta / x - self.twist_w / (x**2)
        b = 2 * x * a - beta
        w = self.twist_w + b * const.Ts
        self.twist_w = w
        if signed_x < 0:
            w *= -1
        # print(abs(w))
        waypoints[kicker_id] = twist.spin_with_ball(w)
        if x > const.KICK_ALIGN_ANGLE:
            field.allies[kicker_id].set_dribbler_speed(max(5, 15 - abs(w) * 5))
            self.wait_kick_timer = None
        else:
            # if self.wait_kick_timer is None:
            #     self.wait_kick_timer = time()
            # else:
            #     wt = 0.1
                # if time() - self.wait_kick_timer > wt:
            self.wait_kick_timer = None
            field.allies[kicker_id].kick_forward()
                # else:
                #     print(field.allies[kicker_id].dribbler_speed_)
                #     field.allies[kicker_id].set_dribbler_speed(max(6, 15 - (15 / wt) * (time() - self.wait_kick_timer)))

    def estimate_pass_point(self, field: fld.Field, frm: aux.Point, to: aux.Point) -> float:
        """
        Оценивает пас из точки "frm" в точку "to, возвращая положительное значение до 0.8
        """
        if frm is None or to is None:
            return 0
        positions = []
        for rbt in field.allies:
            if rbt.is_used():
                positions.append([rbt.r_id, rbt.get_pos()])
        positions = sorted(positions, key=lambda x: x[1].y)

        tangents = []
        for p in positions:
            tgs = aux.get_tangent_points(p[1], frm, const.ROBOT_R)
            if tgs is None or isinstance(tgs, aux.Point):
                # print("Err while estimate pass point", p, frm, tgs)
                continue
            # print(tgs[0], tgs[1])
            tangents.append([p[0], tgs])

        min_ = 10e3

        shadows_bots = []
        for tangent in tangents:
            ang1 = aux.get_angle_between_points(to, frm, tangent[1][0])
            ang2 = aux.get_angle_between_points(to, frm, tangent[1][1])

            if not ((ang1 >= 180 and ang2 >= 180) or (ang1 <= 180 and ang2 <= 180)):
                shadows_bots.append(tangent[0])

            if ang1 > 180:
                ang1 = 360 - ang1
            if ang2 > 180:
                ang2 = 360 - ang2

            if ang1 < min_:
                min_ = ang1
            if ang2 < min_:
                min_ = ang2
        # if minId == -1:
        #     return 0
        # print(min_, minId)
        if min_ == 10e3:
            return 0
        dist = (frm - to).mag() / 1000
        max_ang = 57.3 * aux.wind_down_angle(2 * math.atan2(const.ROBOT_SPEED, -0.25 * dist + 4.5)) / 2
        return min(abs(min_ / max_ang), 1)

    def choose_kick_point(
        self, field: fld.Field, kicker_id: int, ball_pos: Optional[aux.Point] = None
    ) -> tuple[aux.Point, float]:
        """
        Выбирает оптимальную точку в воротах для удара
        """
        if ball_pos is None:
            ball_pos = field.ball.get_pos()

        positions = []
        for rbt in field.allies:
            if rbt.r_id != kicker_id:
                if (
                    aux.dist(rbt.get_pos(), field.enemy_goal.center) < aux.dist(field.enemy_goal.center, ball_pos)
                    and rbt.is_used()
                ):
                    positions.append(rbt.get_pos())
        for rbt in field.enemies:
            if (
                aux.dist(rbt.get_pos(), field.enemy_goal.center) < aux.dist(field.enemy_goal.center, ball_pos)
                and rbt.is_used()
            ):
                positions.append(rbt.get_pos())

        positions = sorted(positions, key=lambda x: x.y)

        segments = [field.enemy_goal.up]
        for p in positions:
            tangents = aux.get_tangent_points(p, ball_pos, const.ROBOT_R)
            if tangents is None or isinstance(tangents, aux.Point):
                print(p, ball_pos, tangents)
                continue

            int1 = aux.get_line_intersection(
                ball_pos,
                tangents[0],
                field.enemy_goal.down,
                field.enemy_goal.up,
                "RS",
            )
            int2 = aux.get_line_intersection(
                ball_pos,
                tangents[1],
                field.enemy_goal.down,
                field.enemy_goal.up,
                "RS",
            )
            if int1 is None and int2 is not None:
                segments.append(field.enemy_goal.up)
                segments.append(int2)
            elif int1 is not None and int2 is None:
                segments.append(int1)
                segments.append(field.enemy_goal.down)
            elif int1 is not None and int2 is not None:
                segments.append(int1)
                segments.append(int2)

        segments.append(field.enemy_goal.down)
        max_ = 0.0
        maxId = -1
        for i in range(0, len(segments), 2):
            c = segments[i]
            a = segments[i + 1]
            b = ball_pos
            if c.y > a.y:
                continue  # Shadow intersection
            ang = aux.get_angle_between_points(a, b, c)
            # print(ang, c.y, a.y)
            if ang > max_:
                max_ = ang
                maxId = i

        if maxId == -1:
            return field.enemy_goal.center, 0

        A = segments[maxId + 1]
        B = ball_pos
        C = segments[maxId]
        tmp1 = (C - B).mag()
        tmp2 = (A - B).mag()
        CA = A - C
        pnt = C + CA * 0.5 * (tmp1 / tmp2)
        self.image.draw_dot(pnt, 10, (255, 0, 0))

        if max_ > 180:
            max_ = 360 - max_

        dist = (ball_pos - pnt).mag() / 1000
        max_ang = 57.3 * aux.wind_down_angle(2 * math.atan2(const.ROBOT_SPEED, -0.25 * dist + 4.5)) / 2

        # print(max_, max_ang, max_ / max_ang)
        return pnt, min(max_ / max_ang, 1)

    def goalk(
        self,
        field: fld.Field,
        waypoints: list[wp.Waypoint],
        gk_wall_idx_list: list[int],
        robot_with_ball: Optional[robot.Robot],
    ) -> None:
        """
        Управление вратарём
        """
        gk_pos = None
        if robot_with_ball is not None:
            predict = aux.get_line_intersection(
                robot_with_ball.get_pos(),
                robot_with_ball.get_pos() + aux.rotate(aux.RIGHT, robot_with_ball.get_angle()),
                field.ally_goal.down,
                field.ally_goal.up,
                "RS",
            )
            if predict is not None:
                p_ball = (field.ball.get_pos() - predict).unity()
                gk_pos = aux.lerp(
                    aux.point_on_line(field.ally_goal.center, field.ball.get_pos(), const.GK_FORW),
                    p_ball * const.GK_FORW
                    + aux.get_line_intersection(
                        robot_with_ball.get_pos(),
                        robot_with_ball.get_pos() + aux.rotate(aux.RIGHT, robot_with_ball.get_angle()),
                        field.ally_goal.down,
                        field.ally_goal.up,
                        "RS",
                    ),
                    0.5,
                )

        if (
            field.is_ball_moves_to_goal()
            and self.ball_start_point is not None
            and (self.ball_start_point - field.ball.get_pos()).mag() > const.INTERCEPT_SPEED
        ):
            tmp_pos = aux.get_line_intersection(
                self.ball_start_point, field.ball.get_pos(), field.ally_goal.down, field.ally_goal.up, "RS"
            )
            if tmp_pos is not None:
                gk_pos = aux.closest_point_on_line(
                    field.ball.get_pos(), tmp_pos, field.allies[gk_wall_idx_list[0]].get_pos()
                )

        if gk_pos is None:
            gk_pos = aux.point_on_line(
                field.ally_goal.center - field.ally_goal.eye_forw * 1000, field.ball.get_pos(), const.GK_FORW + 1000
            )
            gk_pos.x = min(field.ally_goal.center.x + field.ally_goal.eye_forw.x * 300, gk_pos.x, key=lambda x: abs(x))
            if abs(gk_pos.y) > abs(field.ally_goal.up.y):
                gk_pos.y = abs(field.ally_goal.up.y) * abs(gk_pos.y) / gk_pos.y
            self.image.draw_dot(gk_pos, 10, (255, 255, 255))
        else:
            self.image.draw_dot(gk_pos, 10, (0, 0, 0))

        gk_angle = math.pi / 2
        waypoints[gk_wall_idx_list[0]] = wp.Waypoint(gk_pos, gk_angle, wp.WType.S_IGNOREOBSTACLES)

        self.image.draw_dot(field.ball.get_pos(), 5)

        if field.is_ball_stop_near_goal() or field.ally_with_ball == field.allies[const.GK]:
            waypoints[gk_wall_idx_list[0]] = wp.Waypoint(
                field.ball.get_pos(), field.ally_goal.eye_forw.arg(), wp.WType.S_BALL_KICK_UP
            )

        wallline = [field.ally_goal.frw + field.ally_goal.eye_forw * const.GOAL_WALLLINE_OFFSET]
        wallline.append(wallline[0] + field.ally_goal.eye_up)

        walline = aux.point_on_line(field.ally_goal.center, field.ball.get_pos(), const.GOAL_WALLLINE_OFFSET)
        walldir = aux.rotate((field.ally_goal.center - field.ball.get_pos()).unity(), math.pi / 2)
        dirsign = -aux.sign(aux.vec_mult(field.ally_goal.center, field.ball.get_pos()))

        wall = []
        for i in range(len(gk_wall_idx_list) - 1):
            wall.append(walline - walldir * (i + 1) * dirsign * (1 + (i % 2) * -2) * const.GOAL_WALL_ROBOT_SEPARATION)
            waypoints[gk_wall_idx_list[i + 1]] = wp.Waypoint(wall[i], walldir.arg(), wp.WType.S_IGNOREOBSTACLES)

    def reset_all_attack_var(self) -> None:
        """Обнуление глобальных параметров атаки, используется при смене состояния на атаку"""
        # return
        self.used_pop_pos = [False, False, False, False, False]
        self.robot_with_ball = None
        self.connector = []
        # self.popusk = []
        self.attack_state = "TO_BALL"
        self.attack_pos = aux.Point(0, 0)
        self.calc = False
        self.point_res = aux.Point(0, 0)

    # def free_kick(self, field: fld.Field, waypoints: list[wp.Waypoint]) -> None:
    #     """Свободный удар (после любого нарушения/остановки игры) по команде судей"""
    #     wall = []
    #     if not self.refs.we_active:
    #         wall = self.defense(field, waypoints, wp.WType.S_KEEP_BALL_DISTANCE)
    #         self.refs.keep_distance(field, waypoints)
    #     else:
    #         self.state = States.ATTACK
    #         self.decide_popusk_position(field)
    #         self.pre_attack(field)
    #         self.attack(field, waypoints)
    #     robot_with_ball = robot.find_nearest_robot(field.ball.get_pos(), field.enemies)
    #     self.goalk(field, waypoints, [const.GK] + wall, robot_with_ball)

    # def decide_popusk_position(self, field: fld.Field) -> None:
    #     """
    #     Выбор ролей для нападающих
    #     """
    #     for point_ind, popusk_position in enumerate(field.enemy_goal.popusk_positions):
    #         save_robot = -1
    #         if not self.used_pop_pos[point_ind]:
    #             mn = 1e10
    #             for robo in field.allies:
    #                 if (
    #                     robo.is_used()
    #                     and robo.r_id != const.GK
    #                     and robo.r_id != self.robot_with_ball
    #                     and not (robo.r_id in self.popusk)
    #                 ):
    #                     pop_pos_dist = aux.dist(robo.get_pos(), popusk_position)
    #                     if mn > pop_pos_dist:
    #                         mn = pop_pos_dist
    #                         save_robot = robo.r_id
    #                 elif not robo.is_used() and field.allies[robo.r_id].role != 0:
    #                     self.used_pop_pos[robo.role] = False
    #                     field.allies[robo.r_id].role = 0
    #                     if robo.r_id in self.popusk:
    #                         self.popusk.pop(self.popusk.index(robo.r_id))
    #         if save_robot != -1:
    #             field.allies[save_robot].role = point_ind
    #             self.popusk.append(save_robot)
    #             self.used_pop_pos[point_ind] = True

    #     # print(used_pop_pos)
    #     # print(self.popusk)

    # def pre_attack(self, field: fld.Field) -> None:
    #     """
    #     Выбор атакующего робота
    #     """
    #     if self.robot_with_ball is not None and field.ball.get_vel().mag() < 800:
    #         mn = 1e10
    #         for robo in field.allies:
    #             if robo.is_used() and robo.r_id != const.GK:
    #                 ball_dist = aux.dist(field.ball.get_pos(), robo.get_pos())
    #                 if mn > ball_dist:
    #                     mn = ball_dist
    #                     self.robot_with_ball = robo.r_id
    #         if self.robot_with_ball in self.popusk:
    #             self.popusk.pop(self.popusk.index(self.robot_with_ball))
    #     elif self.robot_with_ball is not None and not field.allies[self.robot_with_ball].is_used():
    #         field.allies[self.robot_with_ball].role = 0
    #         self.robot_with_ball = None

    # def attack(self, field: fld.Field, waypoints: list[wp.Waypoint]) -> None:
    #     """Атака"""
    #     for robo in self.popusk:
    #         pop_pos = field.allies[robo].role
    #         waypoints[robo] = wp.Waypoint(
    #             field.enemy_goal.popusk_positions[pop_pos],
    #             aux.angle_to_point(field.allies[robo].get_pos(), field.enemy_goal.center),
    #             wp.WType.S_ENDPOINT,
    #         )
    #     # print(self.point_res)
    #     if self.robot_with_ball is not None:
    #         self.attack_pos = field.ball.get_pos()
    #         if self.attack_state == "TO_BALL":
    #             self.point_res = field.enemy_goal.center
    #             if aux.in_place(self.attack_pos, field.allies[self.robot_with_ball].get_pos(), 1000):
    #                 self.attack_state = "CALCULATING"

    #         elif self.attack_state == "CALCULATING":
    #             self.point_res = self.choose_kick_point(field, self.robot_with_ball)[0]
    #             if self.point_res is None:
    #                 print("self.point_res is None")
    #                 self.point_res = field.enemy_goal.center
    #             self.attack_state = "GO_TO_SHOOTING_POSITION"

    #         elif self.attack_state == "GO_TO_SHOOTING_POSITION":
    #             if aux.in_place(self.attack_pos, field.allies[self.robot_with_ball].get_pos(), 50):
    #                 self.attack_state = "SHOOT"

    #         elif self.attack_state == "SHOOT":
    #             self.attack_pos = field.ball.get_pos()
    #             if not aux.in_place(self.attack_pos, field.allies[self.robot_with_ball].get_pos(), 3000):
    #                 self.popusk.append(self.robot_with_ball)
    #                 self.attack_state = "TO_BALL"
    #                 self.robot_with_ball = None
    #         # ехать к нужной точке
    #         if self.robot_with_ball is not None:
    #             waypoints[self.robot_with_ball] = wp.Waypoint(
    #                 field.ball.get_pos(), aux.angle_to_point(field.ball.get_pos(), self.point_res), wp.WType.S_BALL_KICK
    #             )

    # def defense(
    #     self, field: fld.Field, waypoints: list[wp.Waypoint], ENDPOINT_TYPE: wp.WType = wp.WType.S_ENDPOINT
    # ) -> list[int]:
    #     """Защита"""
    #     dist_between = 200

    #     works_robots = []
    #     for i in range(const.TEAM_ROBOTS_MAX_COUNT):
    #         if field.allies[i].is_used():
    #             field.allies[i].dribbler_enable_ = 0
    #             field.allies[i].auto_kick_ = 0
    #             works_robots.append(field.allies[i])
    #     total_robots = len(works_robots)

    #     used_robots_id = []
    #     if field.allies[const.GK].is_used():
    #         used_robots_id = [const.GK]
    #     robot_with_ball = robot.find_nearest_robot(field.ball.get_pos(), field.enemies)

    #     def1 = robot.find_nearest_robot(field.ball.get_pos(), field.allies, used_robots_id)

    #     # FOR BALL STEALING
    #     target_point = aux.point_on_line(field.ball.get_pos(), field.ally_goal.center, dist_between)
    #     if ENDPOINT_TYPE == wp.WType.S_KEEP_BALL_DISTANCE:
    #         if aux.dist(target_point, field.ball.get_pos()) < const.KEEP_BALL_DIST:
    #             target_point = aux.point_on_line(
    #                 aux.point_on_line(target_point, field.ball.get_pos(), -const.KEEP_BALL_DIST),
    #                 aux.Point(field.polarity * const.GOAL_DX, 0),
    #                 dist_between,
    #             )

    #     waypoint = wp.Waypoint(target_point, aux.angle_to_point(target_point, field.ball.get_pos()), ENDPOINT_TYPE)
    #     waypoints[def1.r_id] = waypoint
    #     self.old_def = def1.r_id

    #     used_robots_id.append(def1.r_id)

    #     wall_bots = []
    #     for r in works_robots:
    #         i = r.r_id
    #         if field.allies[i].r_id not in used_robots_id:
    #             wall_bots.append(field.allies[i].r_id)
    #             used_robots_id.append(field.allies[i].r_id)
    #     return sorted(wall_bots)

    #     rbs = sorted(field.enemies, reverse=True, key=lambda x: x.get_pos().x)
    #     rbs_r_ids = []
    #     x_attack = 3500

    #     for r in rbs:
    #         if (
    #             field.polarity * r.get_pos().x > 0
    #             and r.r_id != robot_with_ball.r_id
    #             and r.is_used()
    #             and r.r_id != const.ENEMY_GK
    #         ):
    #             rbs_r_ids.append(r.r_id)

    #     # FOR BALL STEALING
    #     wall_bots = []
    #     for _ in range(3):  # TODO: change number of wall_bots
    #         if total_robots - len(used_robots_id) == 0:
    #             break
    #         wall_bot = robot.find_nearest_robot(field.ally_goal.center, field.allies, used_robots_id)
    #         wall_bots.append(wall_bot.r_id)
    #         used_robots_id.append(wall_bot.r_id)

    #     for i, rbs_r_id in enumerate(rbs_r_ids):
    #         if len(used_robots_id) > total_robots:
    #             defender = robot.find_nearest_robot(field.enemies[rbs_r_id].get_pos(), field.allies, used_robots_id)
    #             used_robots_id.append(defender.r_id)

    #             target_point = aux.point_on_line(field.enemies[rbs_r_id].get_pos(), field.ball.get_pos(), dist_between)
    #             waypoint = wp.Waypoint(target_point, aux.angle_to_point(target_point, field.ball.get_pos()), ENDPOINT_TYPE)
    #             waypoints[defender.r_id] = waypoint

    #     if total_robots - len(used_robots_id) == 1:
    #         for r in works_robots:
    #             i = r.r_id
    #             if i not in used_robots_id:
    #                 target_point = aux.Point(field.polarity * -x_attack, 1500)
    #                 waypoint = wp.Waypoint(
    #                     target_point, aux.angle_to_point(target_point, field.ball.get_pos()), ENDPOINT_TYPE
    #                 )
    #                 waypoints[i] = waypoint
    #                 used_robots_id.append(i)
    #     elif total_robots - len(used_robots_id) >= 2:
    #         def2 = robot.find_nearest_robot(aux.Point(field.polarity * -x_attack, 1500), field.allies, used_robots_id)
    #         used_robots_id.append(def2.r_id)
    #         target_point = aux.Point(field.polarity * -x_attack, 1500)
    #         waypoint = wp.Waypoint(target_point, aux.angle_to_point(target_point, field.ball.get_pos()), ENDPOINT_TYPE)
    #         waypoints[def2.r_id] = waypoint

    #         for r in works_robots:
    #             i = r.r_id
    #             if field.allies[i].r_id not in used_robots_id:
    #                 used_robots_id.append(field.allies[i].r_id)
    #                 target_point = aux.Point(field.polarity * -x_attack, -1500)
    #                 waypoint = wp.Waypoint(
    #                     target_point, aux.angle_to_point(target_point, field.ball.get_pos()), ENDPOINT_TYPE
    #                 )
    #                 waypoints[field.allies[i].r_id] = waypoint
    #                 break

    #     for r in works_robots:
    #         i = r.r_id
    #         if field.allies[i].r_id not in used_robots_id:
    #             wall_bots.append(field.allies[i].r_id)
    #             used_robots_id.append(field.allies[i].r_id)
    #     return sorted(wall_bots)
